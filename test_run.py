
"""
============================================================================
 TESTS DEL BOT DE SNAKE
 Uso:   pytest test_run.py -v
 Con cobertura:
        pytest test_run.py --cov=run --cov-report=term-missing
============================================================================

 Que cubren estos tests:

   1. REGLAS DEL JUEGO  - que el bot entienda el tablero igual que el servidor:
      parseo, deteccion de entidades y sobre todo la regla del digito objetivo
      (el del predecesor ciclico ausente), que es la que costo -500 por jugada
      cuando estaba mal.

   2. SEGURIDAD          - que nunca elija una jugada ilegal ni se encierre.
      Estos son los tests que no pueden fallar jamas: una jugada ilegal son
      -500 propios y +1000 para el rival.

   3. ESTRATEGIA         - que priorice como corresponde: X temprano, digitos
      con multiplicador alto, y que descarte los objetivos cuya carrera pierde.

   4. ROBUSTEZ           - que ante cualquier basura de entrada devuelva una
      direccion valida en vez de crashear (un crash = timeout = penalizacion).

   5. RED                - el bucle de eventos, con un websocket simulado.
============================================================================
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run


# ============================================================================
#  AYUDANTES
# ============================================================================

def board(*rows):
    """Arma el string de tablero tal como lo manda el servidor."""
    return ''.join('|' + r + '|\n' for r in rows)


def td(b, side='A', rows=None, cols=None, **kw):
    """Construye un turn_data completo."""
    lines = [l for l in b.split('\n') if l]
    data = {
        'board': b,
        'rows': rows if rows is not None else len(lines),
        'cols': cols if cols is not None else len(lines[0]) - 2,
        'side': side,
        'remaining_moves': 200,
        'player_1': 'p1', 'score_1': 0, 'multiplier_1': 1,
        'player_2': 'p2', 'score_2': 0, 'multiplier_2': 1,
        'game_id': 'g_test', 'turn_token': 't',
    }
    data.update(kw)
    return data


def apply_move(head, direction):
    dr, dc = run.DIRS[direction]
    return (head[0] + dr, head[1] + dc)


def cell_at(b, cell, rows=None, cols=None):
    grid = run.parse_board(b, rows, cols)
    r, c = cell
    if not (0 <= r < len(grid) and 0 <= c < len(grid[0])):
        return None            # pared
    return grid[r][c]


@pytest.fixture(autouse=True)
def limpiar_estado():
    """Cada test arranca con un cerebro limpio, sin memoria de partidas."""
    run.BRAIN.games.clear()
    run.BRAIN.timings = []
    run.BRAIN.eaten_ok = run.BRAIN.eaten_x = run.BRAIN.eaten_bad = 0
    run.HISTORY.clear()
    yield
    run.BRAIN.games.clear()
    run.HISTORY.clear()


# ============================================================================
#  1. PARSEO DEL TABLERO
# ============================================================================

class TestParseo:

    def test_saca_las_barras_y_arma_la_grilla(self):
        g = run.parse_board(board('ab ', ' cd'))
        assert g == ['ab ', ' cd']

    def test_usa_rows_cols_como_fuente_de_verdad(self):
        # El servidor puede recortar espacios finales: sin rows/cols el bot
        # creeria que el tablero es mas angosto e inventaria una pared.
        g = run.parse_board('|A|\n|  |\n', rows=2, cols=5)
        assert len(g) == 2 and all(len(r) == 5 for r in g)
        assert g[0][0] == 'A'

    def test_rellena_filas_faltantes(self):
        g = run.parse_board('|   |\n', rows=3, cols=3)
        assert len(g) == 3
        assert g[2] == '   '

    def test_recorta_si_vienen_de_mas(self):
        g = run.parse_board('|abc|\n|def|\n|ghi|\n', rows=2, cols=2)
        assert g == ['ab', 'de']

    def test_sin_rows_cols_usa_la_fila_mas_larga(self):
        g = run.parse_board('|abcd|\n|ab|\n')
        assert all(len(r) == 4 for r in g)

    def test_tablero_vacio(self):
        assert run.parse_board('') == []

    def test_scan_encuentra_todas_las_entidades(self):
        e = run.scan(run.parse_board(board('Aa 1', 'X  2', ' bB3')))
        assert e['A'] == (0, 0)
        assert e['a'] == {(0, 1)}
        assert e['B'] == (2, 2)
        assert e['b'] == {(2, 1)}
        assert e['X'] == {(1, 0)}
        assert e['digits'] == {(0, 3): 1, (1, 3): 2, (2, 3): 3}

    def test_scan_ignora_espacios(self):
        e = run.scan(run.parse_board(board('    ', '    ')))
        assert e['A'] is None and not e['digits'] and not e['X']


# ============================================================================
#  2. LA REGLA DEL DIGITO OBJETIVO
#     Verificada contra 21/21 digitos comidos en partidas reales.
# ============================================================================

class TestDigitoObjetivo:

    @pytest.mark.parametrize('digitos,esperado', [
        ({(0, 0): 1, (0, 1): 2, (0, 2): 3, (0, 3): 4, (0, 4): 5}, 1),
        ({(0, 0): 2, (0, 1): 3, (0, 2): 4, (0, 3): 5, (0, 4): 6}, 2),
        ({(0, 0): 5, (0, 1): 6, (0, 2): 7, (0, 3): 8, (0, 4): 9}, 5),
        # ciclico: despues del 9 viene el 1, asi que el objetivo es el 6
        ({(0, 0): 6, (0, 1): 7, (0, 2): 8, (0, 3): 9, (0, 4): 1}, 6),
        ({(0, 0): 9, (0, 1): 1, (0, 2): 2, (0, 3): 3, (0, 4): 4}, 9),
        ({(0, 0): 1, (0, 1): 2, (0, 2): 7, (0, 3): 8, (0, 4): 9}, 7),
    ])
    def test_objetivo_es_el_de_predecesor_ausente(self, digitos, esperado):
        cell, val = run.target_digit(digitos)
        assert val == esperado
        assert digitos[cell] == esperado

    def test_sin_digitos_devuelve_none(self):
        assert run.target_digit({}) == (None, None)

    def test_caso_ambiguo_no_rompe(self):
        # Nunca paso en 595 turnos reales, pero no debe explotar.
        cell, val = run.target_digit({(0, 0): 3, (0, 1): 7})
        assert val in (3, 7)

    def test_secuencia_completa_visible(self):
        # Los 5 digitos son consecutivos: el bot puede planificar la cadena.
        b = board('A   ', '5678', '9   ', '    ')
        d = run.BRAIN.decide(td(b))
        assert d in run.DIRS
        seq = run.BRAIN.last['seq']
        assert [v for _, v in seq] == [5, 6, 7, 8, 9]


# ============================================================================
#  3. SEGUIMIENTO DEL CUERPO
# ============================================================================

class TestBodyTracker:

    def test_arranque_en_frio_camina_el_cuerpo(self):
        t = run.BodyTracker()
        # vibora recta: cabeza en (0,2), cuerpo en (0,1) y (0,0)
        orden = t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        assert orden == [(0, 2), (0, 1), (0, 0)]

    def test_mantiene_el_orden_al_avanzar(self):
        t = run.BodyTracker()
        t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        orden = t.update((0, 3), {(0, 1), (0, 2), (0, 3)})
        assert orden == [(0, 3), (0, 2), (0, 1)]

    def test_detecta_crecimiento(self):
        t = run.BodyTracker()
        t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        orden = t.update((0, 3), {(0, 0), (0, 1), (0, 2), (0, 3)})
        assert orden == [(0, 3), (0, 2), (0, 1), (0, 0)]

    def test_orden_correcto_en_vibora_enroscada(self):
        # Una U: los brazos verticales son adyacentes, asi que una reconstruccion
        # por BFS tomaria el atajo y confundiria cual es la cola. El historial no.
        t = run.BodyTracker()
        t.order = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)]
        cells = {(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)}
        orden = t.update((0, 0), cells)
        assert orden[-1] == (1, 1)      # la cola sigue siendo la misma

    def test_se_recupera_de_una_desincronizacion(self):
        t = run.BodyTracker()
        t.order = [(9, 9), (9, 8)]      # historial que no tiene nada que ver
        orden = t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        assert set(orden) == {(0, 0), (0, 1), (0, 2)}
        assert orden[0] == (0, 2)

    def test_cabeza_none_limpia_el_estado(self):
        t = run.BodyTracker()
        t.update((0, 2), {(0, 1), (0, 2)})
        assert t.update(None, set()) == []


# ============================================================================
#  4. SEGURIDAD - los tests que no pueden fallar nunca
# ============================================================================

class TestSeguridad:

    def test_nunca_elige_una_jugada_ilegal(self):
        """Barrido sobre muchos tableros: toda decision debe ser legal."""
        import random
        rnd = random.Random(7)
        for _ in range(60):
            rows, cols = rnd.randint(12, 20), rnd.randint(12, 20)
            g = [[' '] * cols for _ in range(rows)]
            libres = [(r, c) for r in range(rows) for c in range(cols)]
            rnd.shuffle(libres)
            cuerpo = [libres.pop() for _ in range(6)]
            for i, (r, c) in enumerate(cuerpo[:3]):
                g[r][c] = 'A' if i == 0 else 'a'
            for i, (r, c) in enumerate(cuerpo[3:]):
                g[r][c] = 'B' if i == 0 else 'b'
            for v in range(1, 6):
                r, c = libres.pop(); g[r][c] = str(v)
            for _ in range(2):
                r, c = libres.pop(); g[r][c] = 'X'
            b = ''.join('|' + ''.join(row) + '|\n' for row in g)
            data = td(b, 'A', rows, cols)
            run.BRAIN.games.clear()
            d = run.choose_direction(data)
            ent = run.scan(run.parse_board(b, rows, cols))
            ocupadas = ent['a'] | ent['b'] | {ent['A'], ent['B']}
            destino = apply_move(ent['A'], d)
            assert 0 <= destino[0] < rows and 0 <= destino[1] < cols, \
                'choco contra la pared'
            assert destino not in ocupadas, 'choco contra un cuerpo'

    def test_no_se_mete_en_un_callejon_sin_salida(self):
        # Corredor de una sola casilla a la derecha; abajo hay campo abierto.
        b = board(
            'aaaaaaA   ',
            'aaaaaaa   ',
            '          ',
            '   1  2   ',
            '   3  4   ',
            '   5      ',
            '      X  X',
            '          ',
            '   Bbb    ',
            '          ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d != 'left'      # left seria meterse contra su propio cuerpo

    def test_prefiere_la_salida_con_mas_aire(self):
        # Arriba: bolsillo cerrado de 2 casillas. Abajo: tablero abierto.
        b = board(
            '  #       '.replace('#', ' '),
            ' a a      ',
            ' aAa      ',
            '          ',
            '   1  2   ',
            '   3  4   ',
            '   5      ',
            '  X    X  ',
            '   Bbb    ',
            '          ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d == 'down'

    def test_sin_salidas_no_explota(self):
        # Encerrado por su propio cuerpo: igual debe devolver algo valido.
        b = board(
            ' a  ',
            'aAa ',
            ' a  ',
            '    ',
        )
        d = run.choose_direction(td(b, 'A'))
        assert d in run.DIRS

    def test_desesperado_evita_la_pared_si_puede(self):
        d = run.Brain._desperate((0, 0), 5, 5, {(0, 1), (1, 0)})
        assert d in ('down', 'right')

    def test_is_safe_detecta_encierro(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        # cuerpo largo que rodea a la cabeza en una bolsa de 2 casillas
        cuerpo = [(1, 1), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1), (2, 0), (1, 0)]
        ok, area = br._is_safe(cuerpo, [])
        assert not ok

    def test_is_safe_acepta_campo_abierto(self):
        br = run.Brain()
        br._setup_grid(10, 10)
        ok, area = br._is_safe([(5, 5), (5, 4), (5, 3)], [])
        assert ok and area > 50

    def test_la_cabeza_rival_cuenta_como_amenaza(self):
        br = run.Brain()
        br._setup_grid(5, 5)
        mio = [(2, 2), (2, 1), (2, 0)]
        sin = br._is_safe(mio, [])[1]
        con = br._is_safe(mio, [(0, 0)], opp_head=(0, 0))[1]
        assert con <= sin


# ============================================================================
#  5. NO COMER DIGITOS EQUIVOCADOS  (-500 cada uno)
# ============================================================================

class TestDigitosEquivocados:

    def test_no_pisa_un_digito_que_no_es_el_objetivo(self):
        # El 3 esta pegado a la cabeza pero el objetivo es el 1.
        b = board(
            'aA3       ',
            '          ',
            '     1    ',
            '          ',
            '  2    4  ',
            '        5 ',
            '  X    X  ',
            '          ',
            '   Bbb    ',
            '          ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d != 'right'

    def test_va_al_objetivo_cuando_esta_al_lado(self):
        b = board(
            'aA1       ',
            '          ',
            '     2    ',
            '          ',
            '  3    4  ',
            '        5 ',
            '          ',
            '          ',
            '   Bbb    ',
            '          ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d == 'right'

    def test_en_muchos_tableros_nunca_come_uno_malo(self):
        import random
        rnd = random.Random(11)
        malos = 0
        for _ in range(40):
            rows = cols = 14
            g = [[' '] * cols for _ in range(rows)]
            libres = [(r, c) for r in range(rows) for c in range(cols)]
            rnd.shuffle(libres)
            cuerpo = [libres.pop() for _ in range(6)]
            for i, (r, c) in enumerate(cuerpo[:3]):
                g[r][c] = 'A' if i == 0 else 'a'
            for i, (r, c) in enumerate(cuerpo[3:]):
                g[r][c] = 'B' if i == 0 else 'b'
            base = rnd.randint(1, 9)
            for k in range(5):
                v = (base - 1 + k) % 9 + 1
                r, c = libres.pop(); g[r][c] = str(v)
            for _ in range(2):
                r, c = libres.pop(); g[r][c] = 'X'
            b = ''.join('|' + ''.join(row) + '|\n' for row in g)
            run.BRAIN.games.clear()
            d = run.BRAIN.decide(td(b, 'A', rows, cols))
            ent = run.scan(run.parse_board(b, rows, cols))
            tgt, _ = run.target_digit(ent['digits'])
            destino = apply_move(ent['A'], d)
            if destino in ent['digits'] and destino != tgt:
                malos += 1
        assert malos == 0, '{} jugadas pisaron un digito equivocado'.format(malos)


# ============================================================================
#  6. VALORACION Y ESTRATEGIA
# ============================================================================

class TestValoracion:

    def test_una_X_vale_mucho_mas_que_su_bonus_al_principio(self):
        br = run.Brain()
        br._setup_grid(16, 16)
        # +50 nominal, pero el multiplicador aplica a todos los digitos futuros
        assert br._x_value(150) > 3000

    def test_la_X_pierde_valor_hacia_el_final(self):
        br = run.Brain()
        br._setup_grid(16, 16)
        assert br._x_value(150) > br._x_value(50) > br._x_value(2)
        assert br._x_value(0) == 50.0

    def test_future_base_es_cero_sin_movimientos(self):
        br = run.Brain()
        br._setup_grid(16, 16)
        assert br.future_base(0) == 0.0
        assert br.future_base(-5) == 0.0

    def test_un_9_vale_nueve_veces_un_1(self):
        br = run.Brain()
        assert br._digit_value(9, 1, 0) == 9 * br._digit_value(1, 1, 0)

    def test_el_multiplicador_escala_el_valor(self):
        br = run.Brain()
        assert br._digit_value(5, 3, 0) == 3 * br._digit_value(5, 1, 0)

    def test_negacion_suma_valor_si_el_rival_tiene_multiplicador_alto(self):
        br = run.Brain()
        assert br._digit_value(5, 1, 10) > br._digit_value(5, 1, 1)

    def test_la_tasa_adaptativa_escala_con_el_tablero(self, monkeypatch):
        monkeypatch.setattr(run, 'DIGIT_RATE_ADAPTIVE', True)
        chico, grande = run.Brain(), run.Brain()
        chico._setup_grid(12, 12)
        grande._setup_grid(20, 20)
        assert chico._rate > grande._rate

    def test_distancia_esperada_es_minima_en_el_centro(self):
        br = run.Brain()
        br._setup_grid(15, 15)
        assert br.exp_dist[(7, 7)] == pytest.approx(0.0, abs=1e-9)
        assert br.exp_dist[(0, 0)] > br.exp_dist[(7, 7)]
        assert br.exp_dist[(0, 0)] == pytest.approx(br.exp_dist[(14, 14)])

    def test_typical_value_promedia_lo_disponible(self):
        br = run.Brain()
        br._setup_grid(16, 16)
        v = br._typical_value([((0, 0), 9)], 100, 1, 1)
        assert v > 0


class TestCarreras:

    @pytest.mark.parametrize('k,ko,side,pierde', [
        (5, 5, 'A', False),   # empate: A mueve primero, lo gana
        (5, 5, 'B', True),    # empate: B mueve segundo, lo pierde
        (6, 5, 'A', True),
        (4, 5, 'B', False),
        (4, 5, 'A', False),
    ])
    def test_el_empate_lo_gana_quien_mueve_primero(self, k, ko, side, pierde):
        assert run.Brain._loses_race(k, ko, side) is pierde

    def test_descarta_el_objetivo_cuya_carrera_pierde(self):
        # El 1 esta pegado al rival y lejisimo mio: no debo ir para alla.
        b = board(
            'aaA            ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
            '  X            ',
            '               ',
            '            1B ',
            '           bb  ',
            '     2         ',
            '   3       X   ',
            '  4            ',
            '        5      ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        elegido = run.BRAIN.last['best']
        # deberia ir por la X de arriba a la izquierda, no por el 1 del rival
        assert elegido is None or elegido['cell'] != (9, 12)


class TestEstrategia:

    def test_prioriza_la_X_al_principio_de_la_partida(self):
        # X a 3 casillas, digito objetivo (1) a 3 casillas: la X vale mucho mas.
        b = board(
            '   X   1       ',
            '               ',
            '               ',
            'aaA            ',
            '               ',
            '     2    3    ',
            '               ',
            '        4      ',
            '   5           ',
            '               ',
            '          X    ',
            '               ',
            '               ',
            '        Bbb    ',
            '               ',
        )
        run.BRAIN.decide(td(b, 'A', remaining_moves=290))
        best = run.BRAIN.last['best']
        assert best is not None and best['kind'] == 'X'

    def test_prioriza_el_digito_con_multiplicador_alto_al_final(self):
        # Mismo tablero pero con multiplicador 12 y pocos movimientos:
        # ahora el 9 vale 10800 y la X solo unos 50.
        b = board(
            '   X   9       ',
            '               ',
            '               ',
            'aaA            ',
            '               ',
            '     1    2    ',
            '               ',
            '        3      ',
            '   4           ',
            '               ',
            '          X    ',
            '               ',
            '               ',
            '        Bbb    ',
            '               ',
        )
        run.BRAIN.decide(td(b, 'A', remaining_moves=20, multiplier_1=12))
        best = run.BRAIN.last['best']
        assert best is not None and best['kind'] in ('digit', 'skip')

    def test_encadena_varios_objetivos(self):
        b = board(
            'aaA            ',
            '  1            ',
            '  2            ',
            '  3            ',
            '  4            ',
            '  5            ',
            '               ',
            '      X        ',
            '               ',
            '           X   ',
            '               ',
            '               ',
            '               ',
            '        Bbb    ',
            '               ',
        )
        run.BRAIN.decide(td(b, 'A'))
        best = run.BRAIN.last['best']
        assert best is not None
        assert best['moves'] >= 1

    def test_shift_aproxima_el_cuerpo_tras_avanzar(self):
        orden = [(0, 3), (0, 2), (0, 1), (0, 0)]
        sin_crecer = run.Brain._shift(orden, (0, 5), 2, False)
        assert sin_crecer[0] == (0, 5) and len(sin_crecer) == len(orden) - 1
        creciendo = run.Brain._shift(orden, (0, 5), 2, True)
        assert len(creciendo) == len(sin_crecer) + 1


# ============================================================================
#  7. UTILIDADES INTERNAS
# ============================================================================

class TestInternos:

    def test_bfs_respeta_el_tiempo_de_liberacion(self):
        br = run.Brain()
        br._setup_grid(5, 5)
        # la casilla (0,1) se libera recien en t=3, asi que no se entra en t=1
        dist = br._bfs((0, 0), {(0, 1): 3}, frozenset())
        assert dist[(0, 1)] >= 3

    def test_bfs_esquiva_bloqueos(self):
        br = run.Brain()
        br._setup_grid(3, 3)
        bloq = frozenset({(0, 1), (1, 1), (2, 1)})
        dist = br._bfs((0, 0), {}, bloq)
        assert (0, 2) not in dist          # muro completo: no hay paso

    def test_path_from_reconstruye_el_camino(self):
        br = run.Brain()
        br._setup_grid(4, 4)
        dist, parent = br._bfs((0, 0), {}, frozenset(), with_parents=True)
        camino = br._path_from(parent, (0, 3))
        assert camino[-1] == (0, 3)
        assert len(camino) == dist[(0, 3)]

    def test_path_from_al_origen_es_vacio(self):
        assert run.Brain._path_from({(0, 0): None}, (0, 0)) == []

    def test_advance_mueve_y_hace_crecer(self):
        orden = [(0, 1), (0, 0)]
        sin = run.Brain._advance(orden, [(0, 2)], set())
        assert sin == [(0, 2), (0, 1)]
        con = run.Brain._advance(orden, [(0, 2)], {(0, 2)})
        assert con == [(0, 2), (0, 1), (0, 0)]

    def test_occupancy_libera_la_cola_primero(self):
        occ = run.Brain._occupancy([(0, 2), (0, 1), (0, 0)], [])
        assert occ[(0, 0)] == 1            # la cola se va en 1 paso
        assert occ[(0, 2)] == 3            # la cabeza tarda 3

    def test_occupancy_toma_el_maximo_entre_las_dos(self):
        occ = run.Brain._occupancy([(0, 0)], [(0, 0), (1, 0), (2, 0)])
        assert occ[(0, 0)] == 3

    @pytest.mark.parametrize('destino,esperado', [
        ((0, 1), 'right'), ((0, -1), 'left'), ((1, 0), 'down'), ((-1, 0), 'up'),
    ])
    def test_dir_name(self, destino, esperado):
        assert run.Brain._dir_name((1, 1), (1 + destino[0], 1 + destino[1])) == esperado

    def test_dir_name_con_salto_invalido(self):
        assert run.Brain._dir_name((0, 0), (5, 5)) == 'up'

    def test_setup_grid_cachea(self):
        a, b_ = run.Brain(), run.Brain()
        a._setup_grid(13, 17)
        b_._setup_grid(13, 17)
        assert a.nbrs is b_.nbrs           # misma estructura reutilizada

    def test_vecinos_en_las_esquinas(self):
        br = run.Brain()
        br._setup_grid(5, 5)
        assert len(br.nbrs[(0, 0)]) == 2
        assert len(br.nbrs[(0, 2)]) == 3
        assert len(br.nbrs[(2, 2)]) == 4

    def test_game_crea_y_reutiliza_el_estado(self):
        br = run.Brain()
        g1 = br._game('x')
        assert br._game('x') is g1
        assert br._game('y') is not g1


# ============================================================================
#  8. ROBUSTEZ - nunca crashear
# ============================================================================

class TestRobustez:

    @pytest.mark.parametrize('data', [
        {},
        {'board': ''},
        {'board': '|  |\n', 'side': 'A'},
        {'board': 'basura sin barras', 'side': 'A'},
        {'board': '|A|\n', 'side': 'Z'},
        {'board': None},
        {'board': '|A |\n| B|\n', 'side': 'A', 'rows': 'no es un numero'},
    ])
    def test_siempre_devuelve_una_direccion_valida(self, data):
        assert run.choose_direction(data) in run.DIRS

    def test_sin_cabeza_propia_en_el_tablero(self):
        assert run.choose_direction(td(board('  B ', '    '), 'A')) in run.DIRS

    def test_board_size_cuando_faltan_rows_y_cols(self):
        b = board('A   ', '    ', '   1', '2 3 ')
        data = td(b, 'A')
        del data['rows'], data['cols']
        data['board_size'] = '4x4'
        assert run.BRAIN.decide(data) in run.DIRS

    def test_board_size_malformado(self):
        b = board('A   ', '    ', '   1', '2 3 ')
        data = td(b, 'A')
        del data['rows'], data['cols']
        data['board_size'] = 'no-es-un-tamaño'
        assert run.BRAIN.decide(data) in run.DIRS

    def test_fallback_evita_digitos_malos(self):
        b = board('3A1 ', '  2 ', ' 4 5', '   B')
        d = run._fallback(td(b, 'A'))
        ent = run.scan(run.parse_board(b))
        tgt, _ = run.target_digit(ent['digits'])
        destino = apply_move(ent['A'], d)
        assert destino not in ent['digits'] or destino == tgt

    def test_fallback_sin_cabeza(self):
        assert run._fallback(td(board('    '), 'A')) == 'up'

    def test_fallback_encerrado(self):
        assert run._fallback(td(board(' a  ', 'aAa ', ' a  ', '    '), 'A')) in run.DIRS

    def test_si_el_cerebro_explota_hay_red_de_seguridad(self, monkeypatch):
        def explota(self, data):
            raise RuntimeError('boom')
        monkeypatch.setattr(run.Brain, 'decide', explota)
        assert run.choose_direction(td(board('A   ', '   1', '2 3 ', '45  '), 'A')) in run.DIRS

    def test_si_todo_explota_devuelve_up(self, monkeypatch):
        monkeypatch.setattr(run.Brain, 'decide',
                            lambda self, d: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(run, '_fallback',
                            lambda d: (_ for _ in ()).throw(RuntimeError()))
        assert run.choose_direction({}) == 'up'

    def test_respeta_el_presupuesto_de_tiempo(self):
        import time
        b = board(*(['  ' * 10] * 20))
        grid = [list('  ' * 10) for _ in range(20)]
        grid[0][0] = 'A'; grid[0][1] = 'a'; grid[0][2] = 'a'
        grid[19][19] = 'B'; grid[19][18] = 'b'; grid[19][17] = 'b'
        for i, v in enumerate('12345'):
            grid[3 + i * 3][5] = v
        grid[5][15] = 'X'; grid[15][5] = 'X'
        b = ''.join('|' + ''.join(r) + '|\n' for r in grid)
        t0 = time.perf_counter()
        run.BRAIN.decide(td(b, 'A', 20, 20))
        assert (time.perf_counter() - t0) < run.HARD_BUDGET * 4


# ============================================================================
#  9. CONTABILIDAD Y LOGS
# ============================================================================

class TestContabilidad:

    def test_cuenta_los_digitos_correctos(self):
        b = board('aA1 ', '  2 ', ' 3 4', '5  B')
        run.BRAIN.decide(td(b, 'A'))
        assert run.BRAIN.eaten_ok == 1
        assert run.BRAIN.eaten_bad == 0

    def test_cuenta_las_X(self):
        b = board(
            'aAX            ',
            '               ',
            '   1   2       ',
            '    3     4    ',
            '  5            ',
            '           X   ',
            '               ',
            '               ',
            '               ',
            '        Bbb    ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
        )
        run.BRAIN.decide(td(b, 'A'))
        assert run.BRAIN.eaten_x == 1

    def test_log_event_y_action(self):
        run.log_event('g1', {'event': 'x'})
        run.log_action('g1', {'action': 'move'})
        assert len(run.HISTORY['g1']) == 2
        assert run.HISTORY['g1'][0].startswith('< ')
        assert run.HISTORY['g1'][1].startswith('> ')

    def test_write_game_log_incluye_la_version(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run.log_action('g9', {'action': 'move'})
        run.write_game_log('g9')
        texto = (tmp_path / 'game_g9.log').read_text()
        assert run.BOT_VERSION in texto
        assert '> ' in texto

    def test_write_game_log_no_explota_si_no_puede_escribir(self, monkeypatch):
        monkeypatch.setattr('builtins.open',
                            lambda *a, **k: (_ for _ in ()).throw(OSError('disco lleno')))
        run.write_game_log('g_inexistente')      # no debe lanzar

    def test_write_live_escribe_json_atomico(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run.write_live({'event': 'your_turn', 'board': '|A|'})
        assert json.loads((tmp_path / 'live.json').read_text())['event'] == 'your_turn'
        assert not (tmp_path / 'live.tmp').exists()

    def test_write_live_no_explota_si_falla(self, monkeypatch):
        monkeypatch.setattr('builtins.open',
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        run.write_live({'a': 1})                 # no debe lanzar


# ============================================================================
#  10. CAPA DE RED  (websocket simulado)
# ============================================================================

class FakeWS:
    """Websocket de mentira: entrega mensajes de una lista y guarda los enviados."""

    def __init__(self, entrantes):
        self.entrantes = list(entrantes)
        self.enviados = []

    async def recv(self):
        if not self.entrantes:
            raise ConnectionError('se acabaron los mensajes')
        return self.entrantes.pop(0)

    async def send(self, msg):
        self.enviados.append(json.loads(msg))


def ev(event, data):
    return json.dumps({'event': event, 'data': data})


class TestRed:

    def test_send_serializa_bien(self):
        ws = FakeWS([])
        asyncio.run(run.send(ws, 'move', {'direction': 'up'}))
        assert ws.enviados == [{'action': 'move', 'data': {'direction': 'up'}}]

    def test_acepta_los_desafios(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('challenge', {'challenge_id': 'c1'})])
        asyncio.run(run.play(ws))
        assert ws.enviados[0]['action'] == 'accept_challenge'
        assert ws.enviados[0]['data']['challenge_id'] == 'c1'

    def test_responde_el_turno_con_un_movimiento(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = td(board('aA  ', '  1 ', '2 3 ', '45 B'), 'A')
        ws = FakeWS([ev('your_turn', data)])
        asyncio.run(run.play(ws))
        env = ws.enviados[0]
        assert env['action'] == 'move'
        assert env['data']['direction'] in run.DIRS
        assert env['data']['turn_token'] == data['turn_token']
        assert env['data']['game_id'] == data['game_id']

    def test_ignora_las_listas_de_usuarios(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('list_users', {'users': []}),
                     ev('update_user_list', {'users': []})])
        asyncio.run(run.play(ws))
        assert ws.enviados == []

    def test_game_over_guarda_el_log_y_resetea(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run.BRAIN.eaten_ok = 3
        run.BRAIN.eaten_bad = 1
        run.BRAIN.timings = [0.001, 0.002]
        fin = {'game_id': 'gz', 'board': '|A|', 'score_1': 100, 'score_2': 50,
               'multiplier_1': 2, 'multiplier_2': 1, 'winner': 'p1', 'player_1': 'p1'}
        ws = FakeWS([ev('game_over', fin)])
        asyncio.run(run.play(ws))
        assert (tmp_path / 'game_gz.log').exists()
        assert run.BRAIN.eaten_ok == 0 and run.BRAIN.eaten_bad == 0
        assert run.BRAIN.timings == []

    def test_game_over_sin_game_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('game_over', {'board': '|A|', 'winner': 'p1'})])
        asyncio.run(run.play(ws))        # no debe explotar

    def test_evento_de_error_del_servidor(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('error', {'msg': 'token invalido'})])
        asyncio.run(run.play(ws))
        assert ws.enviados == []

    def test_partida_completa_de_punta_a_punta(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        b = board(
            'aaA            ',
            '               ',
            '   1   2       ',
            '    3     4    ',
            '  5            ',
            '     X     X   ',
            '               ',
            '               ',
            '               ',
            '        Bbb    ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
        )
        mensajes = [ev('challenge', {'challenge_id': 'c9'})]
        for i in range(5):
            mensajes.append(ev('your_turn', td(b, 'A', remaining_moves=300 - 2 * i)))
        mensajes.append(ev('game_over', {
            'game_id': 'g_test', 'board': b, 'score_1': 5000, 'score_2': 100,
            'multiplier_1': 4, 'multiplier_2': 1, 'winner': 'p1', 'player_1': 'p1'}))
        ws = FakeWS(mensajes)
        asyncio.run(run.play(ws))
        movs = [m for m in ws.enviados if m['action'] == 'move']
        assert len(movs) == 5
        assert all(m['data']['direction'] in run.DIRS for m in movs)
        assert (tmp_path / 'game_g_test.log').exists()

    def test_start_se_reconecta_y_corta(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        intentos = {'n': 0}

        class CtxRoto:
            async def __aenter__(self_):
                intentos['n'] += 1
                if intentos['n'] >= 2:
                    raise KeyboardInterrupt
                raise ConnectionError('caida')

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(run, 'websockets', type('M', (), {
            'connect': staticmethod(lambda uri: CtxRoto())})())
        monkeypatch.setattr(run.time, 'sleep', lambda s: None)
        asyncio.run(run.start('token_falso'))
        assert intentos['n'] >= 2

    def test_start_juega_y_termina(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('list_users', {'users': []})])
        estado = {'vueltas': 0}

        class Ctx:
            async def __aenter__(self_):
                estado['vueltas'] += 1
                if estado['vueltas'] > 1:
                    raise KeyboardInterrupt
                return ws

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(run, 'websockets', type('M', (), {
            'connect': staticmethod(lambda uri: Ctx())})())
        monkeypatch.setattr(run.time, 'sleep', lambda s: None)
        asyncio.run(run.start('token'))
        assert estado['vueltas'] >= 1


# ============================================================================
#  11. MODO SUPERVIVENCIA
# ============================================================================

class TestSupervivencia:

    def test_elige_algo_valido_cuando_no_hay_plan(self):
        br = run.Brain()
        br._setup_grid(6, 6)
        base = {
            'up':   {'cell': (1, 2), 'order': [(1, 2), (2, 2)], 'safe': True,  'area': 30},
            'down': {'cell': (3, 2), 'order': [(3, 2), (2, 2)], 'safe': False, 'area': 3},
        }
        d = br._survival(base, [(2, 2), (2, 1)], [], None,
                         set(), (0, 0), None, 6, 6, 0, 0, 100)
        assert d == 'up'

    def test_penaliza_pisar_un_digito_equivocado(self):
        br = run.Brain()
        br._setup_grid(6, 6)
        base = {
            'up':   {'cell': (1, 2), 'order': [(1, 2)], 'safe': True, 'area': 30},
            'down': {'cell': (3, 2), 'order': [(3, 2)], 'safe': True, 'area': 30},
        }
        d = br._survival(base, [(2, 2)], [], None,
                         {(1, 2)}, None, None, 6, 6, 0, 0, 100)
        assert d == 'down'

    def test_se_aleja_de_la_cabeza_rival(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = {
            'up':   {'cell': (3, 4), 'order': [(3, 4)], 'safe': True, 'area': 40},
            'down': {'cell': (5, 4), 'order': [(5, 4)], 'safe': True, 'area': 40},
        }
        d = br._survival(base, [(4, 4)], [(2, 4)], (2, 4),
                         set(), None, None, 8, 8, 0, 0, 100)
        assert d == 'down'


# ============================================================================
#  12. PARTIDAS REALES  (si estan los logs a mano)
# ============================================================================

LOGS = '/mnt/user-data/uploads'


def logs_disponibles():
    import glob
    return sorted(glob.glob(os.path.join(LOGS, 'game_*.log')))


@pytest.mark.skipif(not logs_disponibles(), reason='no hay logs de partidas reales')
class TestPartidasReales:

    def test_nunca_come_mal_ni_juega_ilegal_en_tableros_reales(self):
        errores = ilegales = turnos = 0
        for fn in logs_disponibles():
            run.BRAIN.games.clear()
            for linea in open(fn, encoding='utf-8'):
                linea = linea.rstrip('\r\n')
                if not linea.startswith('< '):
                    continue
                obj = json.loads(linea[2:])
                if obj.get('event') != 'your_turn':
                    continue
                data = obj['data']
                turnos += 1
                d = run.choose_direction(data)
                ent = run.scan(run.parse_board(data['board'], data['rows'], data['cols']))
                cabeza = ent[data['side'].upper()]
                destino = apply_move(cabeza, d)
                ocupadas = ent['a'] | ent['b'] | {c for c in (ent['A'], ent['B']) if c}
                if not (0 <= destino[0] < data['rows'] and 0 <= destino[1] < data['cols']) \
                        or destino in ocupadas:
                    ilegales += 1
                tgt, _ = run.target_digit(ent['digits'])
                if destino in ent['digits'] and destino != tgt:
                    errores += 1
        assert turnos > 0
        assert ilegales == 0, '{} jugadas ilegales'.format(ilegales)
        assert errores == 0, '{} digitos equivocados'.format(errores)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))


# ============================================================================
#  13. RAMAS DEFENSIVAS Y LIMITES DE TIEMPO
# ============================================================================

class TestRamasDefensivas:

    def test_board_size_con_numeros_invalidos(self):
        b = board('A   ', '    ', '   1', '2 3 ')
        data = td(b, 'A')
        del data['rows'], data['cols']
        data['board_size'] = '4xZZ'          # tiene la 'x' pero no son numeros
        assert run.BRAIN.decide(data) in run.DIRS

    def test_secuencia_cortada_si_los_digitos_no_son_consecutivos(self):
        # Solo 1 y 5: la cadena se corta despues del primero.
        b = board('aA  ', '    ', ' 1  ', '  5B')
        run.BRAIN.decide(td(b, 'A'))
        assert len(run.BRAIN.last['seq']) == 1

    def test_cuenta_el_digito_malo_cuando_no_hay_alternativa(self):
        # Unica salida legal: la casilla del 2, que no es el objetivo (lo es el 1).
        b = board('A2 ', 'a  ', '  1')
        d = run.BRAIN.decide(td(b, 'A'))
        assert d == 'right'
        assert run.BRAIN.eaten_bad == 1

    def test_desesperado_en_un_tablero_de_una_casilla(self):
        assert run.Brain._desperate((0, 0), 1, 1, set()) == 'up'

    def test_supervivencia_penaliza_los_bordes(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = {
            'up':   {'cell': (0, 4), 'order': [(0, 4)], 'safe': True, 'area': 40},
            'down': {'cell': (2, 4), 'order': [(2, 4)], 'safe': True, 'area': 40},
        }
        d = br._survival(base, [(1, 4)], [], None,
                         set(), None, None, 8, 8, 0, 0, 100)
        assert d == 'down'                   # (0,4) esta contra la pared de arriba

    def test_filtro_duro_descarta_lo_que_el_rival_tiene_al_lado(self, monkeypatch):
        monkeypatch.setattr(run, 'RACE_HARD_FILTER', True)
        b = board(
            'aaA            ',
            '               ',
            '               ',
            '               ',
            '          2    ',
            '        3      ',
            '      4        ',
            '       1B      ',
            '       Xbb     ',
            '     5         ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d in run.DIRS
        # el 1 y la X estan pegados al rival: no deberia elegirlos como plan
        best = run.BRAIN.last['best']
        assert best is None or best['cell'] not in {(7, 7), (8, 7)}

    def test_sin_tiempo_cae_a_supervivencia(self, monkeypatch):
        monkeypatch.setattr(run, 'HARD_BUDGET', -1.0)
        b = board(
            'aaA            ',
            '   1           ',
            '     2         ',
            '       3       ',
            '         4     ',
            '           5   ',
            '     X         ',
            '          X    ',
            '               ',
            '        Bbb    ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        assert d in run.DIRS
        assert run.BRAIN.last['survival'] is True

    def test_la_busqueda_corta_cuando_se_acaba_el_presupuesto(self, monkeypatch):
        """Reloj falso: las primeras lecturas van bien y despues se dispara,
        para forzar el corte por tiempo en un tramo profundo de la cadena."""
        lecturas = {'n': 0}
        real = run.time.perf_counter

        def reloj_falso():
            lecturas['n'] += 1
            return 0.0 if lecturas['n'] <= 3 else 1e9

        monkeypatch.setattr(run.time, 'perf_counter', reloj_falso)
        b = board(
            'aaA            ',
            '   1           ',
            '     2         ',
            '       3       ',
            '         4     ',
            '           5   ',
            '     X         ',
            '          X    ',
            '               ',
            '        Bbb    ',
            '               ',
            '               ',
            '               ',
            '               ',
            '               ',
        )
        d = run.BRAIN.decide(td(b, 'A'))
        monkeypatch.setattr(run.time, 'perf_counter', real)
        assert d in run.DIRS

    def test_ctrl_c_corta_el_bucle_de_juego(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        class WSInterrumpido(FakeWS):
            async def recv(self):
                raise KeyboardInterrupt

        asyncio.run(run.play(WSInterrumpido([])))    # debe salir limpio

    @pytest.fixture
    def tablero_carrera_perdida(self):
        return board(
            'aaA            ', '               ', '               ', '               ',
            '          2    ', '        3      ', '      4        ', '       1B      ',
            '       Xbb     ', '     5         ', '               ', '               ',
            '               ', '               ', '               ',
        )

    def test_modo_blando_descuenta_en_vez_de_descartar(self, monkeypatch,
                                                       tablero_carrera_perdida):
        monkeypatch.setattr(run, 'RACE_HARD_FILTER', False)
        d = run.BRAIN.decide(td(tablero_carrera_perdida, 'A'))
        assert d in run.DIRS
        assert run.BRAIN.last['plans'] > 0

    def test_valor_cero_descarta_el_objetivo(self, monkeypatch,
                                             tablero_carrera_perdida):
        # Con descuento 0 el objetivo perdido vale 0 y se descarta igual.
        monkeypatch.setattr(run, 'RACE_HARD_FILTER', False)
        monkeypatch.setattr(run, 'LOST_RACE_DIGIT', 0.0)
        monkeypatch.setattr(run, 'LOST_RACE_X', 0.0)
        assert run.BRAIN.decide(td(tablero_carrera_perdida, 'A')) in run.DIRS

    @pytest.mark.parametrize('lecturas_buenas', [2, 3, 4, 5, 6, 8, 12])
    def test_corta_en_cualquier_punto_al_agotarse_el_tiempo(self, monkeypatch,
                                                           lecturas_buenas):
        """El corte por presupuesto puede caer en cualquier lectura del reloj;
        pruebo varios puntos para que ninguna rama de corte quede sin ejercitar."""
        n = {'i': 0}
        real = run.time.perf_counter

        def reloj():
            n['i'] += 1
            return 0.0 if n['i'] <= lecturas_buenas else 1e9

        b = board(
            'aaA            ', '   1           ', '     2         ', '       3       ',
            '         4     ', '           5   ', '     X         ', '          X    ',
            '               ', '        Bbb    ', '               ', '               ',
            '               ', '               ', '               ',
        )
        monkeypatch.setattr(run.time, 'perf_counter', reloj)
        try:
            d = run.BRAIN.decide(td(b, 'A'))
        finally:
            monkeypatch.setattr(run.time, 'perf_counter', real)
        assert d in run.DIRS
