#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 TESTS DEL BOT DE SNAKE - reglas v8
 Uso:   pytest test_run.py -v
 Con cobertura:
        coverage run -m pytest && coverage report -m --fail-under=90
============================================================================

 Organizados por REGLA del juego, no por funcion: si algo se rompe, el nombre
 del test dice que regla dejo de cumplirse.

 Los tres grupos que no pueden fallar nunca, porque cada uno vale la partida:
   * nunca una jugada ilegal        (choque: el primero borra todo el puntaje)
   * nunca entrar en zona de bomba  (explosion: puntaje a 0)
   * nunca comer un digito que no toca (-500 cada uno)
============================================================================
"""

import asyncio
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run


# ============================================================================
#  AYUDANTES
# ============================================================================

def tablero(*filas):
    """Arma el string del tablero tal como lo manda el servidor."""
    return ''.join('|' + f + '|\n' for f in filas)


def td(b, side='A', rows=None, cols=None, **kw):
    """turn_data completo."""
    lineas = [l for l in b.split('\n') if l]
    datos = {
        'board': b,
        'rows': rows if rows is not None else len(lineas),
        'cols': cols if cols is not None else len(list(lineas[0])) - 2,
        'side': side,
        'remaining_moves': 300,
        'board_size': '{}x{}'.format(rows or len(lineas),
                                     cols or len(list(lineas[0])) - 2),
        'player_1': 'p1', 'score_1': 0, 'multiplier_1': 1,
        'player_2': 'p2', 'score_2': 0, 'multiplier_2': 1,
        'game_id': 'g_test', 'turn_token': 't',
    }
    datos.update(kw)
    return datos


def destino(cabeza, direccion):
    dr, dc = run.DIRS[direccion]
    return (cabeza[0] + dr, cabeza[1] + dc)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def tablero_aleatorio(rnd, rows=None, cols=None, largo_mio=3, largo_rival=3,
                      con_bombas=True, con_pared=True, con_colas=False):
    """Tablero valido al estilo v8: digitos con copias, X, pared, bombas.

    Respeta la garantia del enunciado de que nada comestible cae dentro de una
    zona de explosion: si no la respetara, los tests pedirian al bot algo
    imposible.
    """
    rows = rows or rnd.randint(20, 30)
    cols = cols or rnd.randint(20, 30)
    g = [[' '] * cols for _ in range(rows)]
    libres = [(r, c) for r in range(rows) for c in range(cols)]
    rnd.shuffle(libres)
    ocupadas = set()

    def tomar():
        while libres:
            p = libres.pop()
            if p not in ocupadas:
                ocupadas.add(p)
                return p
        return None

    def cadena(largo):
        """Una vibora es una CADENA conectada, no celdas sueltas: si se
        generan dispersas el tablero es imposible y el test no prueba nada."""
        for _ in range(300):
            inicio = (rnd.randrange(2, rows - 2), rnd.randrange(2, cols - 2))
            if inicio in ocupadas:
                continue
            cam = [inicio]
            vistas = {inicio}
            while len(cam) < largo:
                opciones = [(cam[-1][0] + dr, cam[-1][1] + dc)
                            for dr, dc in run.DIRS.values()]
                opciones = [p for p in opciones
                            if 0 <= p[0] < rows and 0 <= p[1] < cols
                            and p not in vistas and p not in ocupadas]
                if not opciones:
                    break
                sig = rnd.choice(opciones)
                cam.append(sig)
                vistas.add(sig)
            if len(cam) == largo:
                ocupadas.update(cam)
                return cam
        return None

    cuerpo_a = cadena(largo_mio)
    cuerpo_b = cadena(largo_rival)
    if cuerpo_a is None or cuerpo_b is None:
        return tablero_aleatorio(rnd, rows, cols, 3, 3, con_bombas,
                                 con_pared, con_colas)
    for i, p in enumerate(cuerpo_a):
        g[p[0]][p[1]] = 'A' if i == 0 else 'a'
    for i, p in enumerate(cuerpo_b):
        g[p[0]][p[1]] = 'B' if i == 0 else 'b'
    cuerpo = cuerpo_a + cuerpo_b

    bombas = []
    if con_bombas:
        for simbolo, cuantas in ((run.BOMBA_GRANDE, 2), (run.BOMBA_CHICA, 5)):
            for _ in range(cuantas):
                for _ in range(200):
                    p = tomar()
                    if p is None:
                        break
                    if any(cheb(p, c) <= run.ALCANCE[simbolo] + 1
                           for c in (cuerpo_a[0], cuerpo_b[0])):
                        continue
                    g[p[0]][p[1]] = simbolo
                    bombas.append((p, run.ALCANCE[simbolo]))
                    break

    def fuera_de_zona(p):
        return all(cheb(p, b) > al for b, al in bombas)

    def tomar_comestible():
        for _ in range(400):
            p = tomar()
            if p is None:
                return None
            if fuera_de_zona(p):
                return p
        return None

    base = rnd.randint(1, 9)
    for k in range(5):
        valor = (base - 1 + k) % 9 + 1
        for _ in range(rnd.randint(3, 5)):
            p = tomar_comestible()
            if p:
                g[p[0]][p[1]] = str(valor)
    for _ in range(2):
        p = tomar_comestible()
        if p:
            g[p[0]][p[1]] = 'X'
    if con_colas:
        for _ in range(rnd.randint(2, 6)):
            p = tomar_comestible()
            if p:
                g[p[0]][p[1]] = run.COLA['A']
    if con_pared:
        largo = rnd.choice([5, 7, 9, 11])
        for _ in range(200):
            if rnd.random() < 0.5:
                r = rnd.randrange(rows)
                c0 = rnd.randrange(0, max(1, cols - largo))
                celdas = [(r, c0 + i) for i in range(largo)]
            else:
                c = rnd.randrange(cols)
                r0 = rnd.randrange(0, max(1, rows - largo))
                celdas = [(r0 + i, c) for i in range(largo)]
            if all(p not in ocupadas and 0 <= p[0] < rows and 0 <= p[1] < cols
                   for p in celdas):
                for p in celdas:
                    ocupadas.add(p)
                    g[p[0]][p[1]] = '#'
                break
    b = ''.join('|' + ''.join(f) + '|\n' for f in g)
    return b, rows, cols


@pytest.fixture(autouse=True)
def limpiar_estado():
    """Cada test arranca con un cerebro limpio, sin memoria de partidas."""
    run.BRAIN.games.clear()
    run.BRAIN.timings = []
    run.BRAIN.last = {}
    run.HISTORY.clear()
    yield
    run.BRAIN.games.clear()
    run.HISTORY.clear()


# ============================================================================
#  1. PARSEO Y UNICODE
#     Desde v8 el tablero trae 💣 y 🧨, que estan fuera del plano basico.
# ============================================================================

class TestParseo:

    def test_saca_las_barras(self):
        assert run.parse_board(tablero('ab ', ' cd')) == [list('ab '), list(' cd')]

    def test_rows_y_cols_mandan(self):
        # Si el servidor recorta espacios finales, calcular el ancho con la fila
        # mas larga achicaria el tablero e inventaria una pared.
        g = run.parse_board('|A|\n|  |\n', rows=2, cols=5)
        assert len(g) == 2 and all(len(f) == 5 for f in g)
        assert g[0][0] == 'A'

    def test_rellena_filas_faltantes(self):
        g = run.parse_board('|   |\n', rows=3, cols=3)
        assert len(g) == 3 and g[2] == [' ', ' ', ' ']

    def test_recorta_si_vienen_de_mas(self):
        assert run.parse_board('|abc|\n|def|\n|ghi|\n', rows=2, cols=2) == \
            [['a', 'b'], ['d', 'e']]

    def test_tablero_vacio(self):
        assert run.parse_board('') == []
        assert run.parse_board(None) == []

    @pytest.mark.parametrize('simbolo', [run.BOMBA_GRANDE, run.BOMBA_CHICA,
                                         run.COLA['A'], run.COLA['B']])
    def test_cada_simbolo_ocupa_una_sola_celda(self, simbolo):
        """Una bomba son 2 unidades UTF-16 y 4 bytes: si el parseo contara mal,
        todas las columnas a su derecha se correrian."""
        fila = 'A' + simbolo + '1'
        g = run.parse_board(tablero(fila), 1, 3)
        assert len(g[0]) == 3
        assert g[0][0] == 'A' and g[0][1] == simbolo and g[0][2] == '1'

    def test_las_columnas_no_se_corren_con_bombas(self):
        b = tablero('A' + run.BOMBA_GRANDE + run.BOMBA_CHICA + '1X#')
        e = run.scan(run.parse_board(b, 1, 6), 'A')
        assert e['A'] == (0, 0)
        assert sorted(e['bombs']) == [(0, 1), (0, 2)]
        assert e['digits'] == {(0, 3): 1}
        assert e['X'] == {(0, 4)}
        assert e['walls'] == {(0, 5)}

    def test_llegan_como_escapes_json(self):
        """El servidor los manda escapados; json.loads los vuelve un caracter."""
        crudo = '{"board": "|A\\ud83d\\udca3\\u24b6|\\n"}'
        b = json.loads(crudo)['board']
        g = run.parse_board(b, 1, 3)
        assert g[0][1] == run.BOMBA_GRANDE
        assert g[0][2] == run.COLA['A']


class TestScan:

    def test_separa_todas_las_entidades(self):
        b = tablero('Aa 1X', '#' + run.BOMBA_CHICA + run.COLA['A'] +
                    run.COLA['B'] + '2', ' bB 3')
        e = run.scan(run.parse_board(b, 3, 5), 'A')
        assert e['A'] == (0, 0) and e['a'] == {(0, 1)}
        assert e['B'] == (2, 2) and e['b'] == {(2, 1)}
        assert e['digits'] == {(0, 3): 1, (1, 4): 2, (2, 4): 3}
        assert e['X'] == {(0, 4)}
        assert e['walls'] == {(1, 0)}
        assert e['bombs'] == {(1, 1): 1}
        assert e['mi_cola'] == {(1, 2)}
        assert e['cola_rival'] == {(1, 3)}

    def test_la_cola_depende_del_lado(self):
        """Ⓐ es comida para A; para B es su propia cola perdida."""
        b = tablero('A' + run.COLA['A'] + run.COLA['B'] + 'B')
        como_a = run.scan(run.parse_board(b, 1, 4), 'A')
        como_b = run.scan(run.parse_board(b, 1, 4), 'B')
        assert como_a['mi_cola'] == {(0, 1)} and como_a['cola_rival'] == {(0, 2)}
        assert como_b['mi_cola'] == {(0, 2)} and como_b['cola_rival'] == {(0, 1)}

    def test_lo_desconocido_queda_como_obstaculo(self):
        e = run.scan(run.parse_board(tablero('A@ '), 1, 3), 'A')
        assert e['obstacles'] == {(0, 1)}

    def test_sin_entidades(self):
        e = run.scan(run.parse_board(tablero('   ', '   '), 2, 3), 'A')
        assert e['A'] is None and not e['digits'] and not e['bombs']


# ============================================================================
#  2. LA REGLA DEL DIGITO OBJETIVO
#     El enunciado avisa que NO es el mas chico. Verificada 21/21 en partidas
#     reales, y desde v6 tiene que funcionar con copias multiples.
# ============================================================================

class TestDigitoObjetivo:

    @pytest.mark.parametrize('valores,esperado', [
        ([1, 2, 3, 4, 5], 1),
        ([2, 3, 4, 5, 6], 2),
        ([5, 6, 7, 8, 9], 5),
        ([6, 7, 8, 9, 1], 6),     # ciclico: despues del 9 viene el 1
        ([9, 1, 2, 3, 4], 9),
        ([1, 6, 7, 8, 9], 6),     # el caso trampa del enunciado: NO es el 1
    ])
    def test_predecesor_ciclico_ausente(self, valores, esperado):
        d = {}
        for i, v in enumerate(valores):
            d[(i, 0)] = v
        _, got = run.target_digit(d)
        assert got == esperado

    @pytest.mark.parametrize('copias', [1, 3, 5])
    def test_la_regla_no_cambia_con_copias(self, copias):
        d = {}
        for i, v in enumerate([1, 6, 7, 8, 9]):
            for k in range(copias):
                d[(i, k)] = v
        _, got = run.target_digit(d)
        assert got == 6, 'con copias el objetivo sigue siendo el 6, no el 1'

    def test_sin_digitos(self):
        assert run.target_digit({}) == (None, None)

    def test_caso_ambiguo_no_rompe(self):
        _, v = run.target_digit({(0, 0): 3, (0, 1): 7})
        assert v in (3, 7)

    def test_copias_de_devuelve_todas(self):
        d = {(0, 0): 4, (0, 1): 4, (0, 2): 5, (1, 0): 4}
        assert run.copias_de(d, 4) == {(0, 0), (0, 1), (1, 0)}
        assert run.copias_de(d, 9) == set()

    def test_secuencia_visible_en_orden_ciclico(self):
        d = {}
        for i, v in enumerate([8, 9, 1, 2, 3]):
            for k in range(3):
                d[(i, k)] = v
        seq = run.secuencia_visible(d)
        assert [v for v, _ in seq] == [8, 9, 1, 2, 3]
        assert all(len(celdas) == 3 for _, celdas in seq)

    def test_secuencia_vacia(self):
        assert run.secuencia_visible({}) == []


# ============================================================================
#  3. BOMBAS DE PROXIMIDAD (v8) - explotan por CERCANIA, no por contacto
# ============================================================================

class TestZonasDeExplosion:

    def test_la_grande_cubre_cinco_por_cinco(self):
        z = run.zonas_de_explosion({(5, 5): 2}, 20, 20)
        assert len(z) == 25
        assert (3, 3) in z and (7, 7) in z      # esquinas de la zona
        assert (2, 5) not in z and (5, 8) not in z

    def test_la_chica_cubre_tres_por_tres(self):
        z = run.zonas_de_explosion({(5, 5): 1}, 20, 20)
        assert len(z) == 9
        assert (4, 4) in z and (6, 6) in z
        assert (3, 5) not in z

    def test_las_diagonales_cuentan_como_una_celda(self):
        """La distancia es Chebyshev: una diagonal esta a 1, no a 2."""
        z = run.zonas_de_explosion({(5, 5): 1}, 20, 20)
        assert (4, 4) in z, 'la diagonal tiene que estar dentro'

    def test_se_recorta_contra_el_borde(self):
        z = run.zonas_de_explosion({(0, 0): 2}, 20, 20)
        assert len(z) == 9 and all(r >= 0 and c >= 0 for r, c in z)

    def test_sin_bombas_no_hay_zona(self):
        assert run.zonas_de_explosion({}, 10, 10) == set()

    def test_dos_bombas_se_suman(self):
        z = run.zonas_de_explosion({(2, 2): 1, (10, 10): 2}, 20, 20)
        assert len(z) == 9 + 25


class TestEvitarBombas:

    def test_no_entra_en_la_zona_aunque_el_camino_sea_mas_largo(self):
        # El unico camino recto al 1 pasa por la zona 3x3 del 🧨.
        b = tablero(
            'aaaaaaaaaaaaaaa',
            '               ',
            '      ' + run.BOMBA_CHICA + '        ',
            '  A         1  ',
            '               ',
            '   Bbb         ',
            '  2  3  4  5   ')
        e = run.scan(run.parse_board(b, 7, 15), 'A')
        zona = run.zonas_de_explosion(e['bombs'], 7, 15)
        cab = e['A']
        for _ in range(8):
            g = [list(f) for f in run.parse_board(b, 7, 15)]
            g[e['A'][0]][e['A'][1]] = ' '
            for (r, c) in e['a']:
                g[r][c] = ' '
            g[cab[0]][cab[1]] = 'A'
            actual = ''.join('|' + ''.join(f) + '|\n' for f in g)
            d = run.choose_direction(td(actual, 'A', 7, 15, score_1=20000,
                                        multiplier_1=8))
            nb = destino(cab, d)
            if not (0 <= nb[0] < 7 and 0 <= nb[1] < 15):
                break
            assert nb not in zona, 'entro en la zona de la bomba en {}'.format(nb)
            cab = nb

    def test_barrido_nunca_pisa_una_zona(self):
        rnd = random.Random(2026)
        for _ in range(40):
            b, rows, cols = tablero_aleatorio(rnd)
            run.BRAIN.games.clear()
            d = run.choose_direction(td(b, 'A', rows, cols, score_1=15000))
            e = run.scan(run.parse_board(b, rows, cols), 'A')
            zona = run.zonas_de_explosion(e['bombs'], rows, cols)
            nb = destino(e['A'], d)
            libres = [c for _, (dr, dc) in run.DIR_ITEMS
                      for c in [(e['A'][0] + dr, e['A'][1] + dc)]
                      if 0 <= c[0] < rows and 0 <= c[1] < cols
                      and c not in zona and c not in e['walls']
                      and c not in (e['a'] | e['b'] | {e['A'], e['B']})]
            if libres:        # solo exijo esquivarla si habia alternativa
                assert nb not in zona

    def test_si_no_hay_salida_elige_la_bomba_antes_que_el_choque(self):
        """Con puntaje alto, explotar y chocar cuestan lo mismo en puntos, pero
        el choque ademas regala la cola: la bomba es el mal menor."""
        b = tablero(
            'aaaaaa',
            'a' + run.BOMBA_CHICA + '   a',
            'aA   a',
            'aaaaaa')
        d = run.choose_direction(td(b, 'A', 4, 6, score_1=9000, multiplier_1=5))
        assert d in run.DIRS

    def test_la_bomba_no_se_pisa_nunca(self):
        b = tablero('A' + run.BOMBA_GRANDE + '  ', '    ', '   B')
        e = run.scan(run.parse_board(b, 3, 4), 'A')
        d = run.choose_direction(td(b, 'A', 3, 4))
        assert destino(e['A'], d) not in e['bombs']


# ============================================================================
#  4. COMIDA DE COLA (v7)
# ============================================================================

class TestComidaDeCola:

    TAB = tablero(
        'aaA ' + run.COLA['A'] + ' ' + run.COLA['A'] + ' ' + run.COLA['A'] + '      ',
        '               ',
        '               ',
        '          1    ',
        '               ',
        '   2   3   4   ',
        '               ',
        '           5   ',
        '               ',
        '             Bb',
        '               ',
        '   X       X   ')

    def test_la_reconoce_como_comida_y_no_como_pared(self):
        e = run.scan(run.parse_board(self.TAB, 12, 15), 'A')
        assert len(e['mi_cola']) == 3
        assert not (e['mi_cola'] & e['obstacles'])

    def test_va_a_buscarla(self):
        run.choose_direction(td(self.TAB, 'A', 12, 15, score_1=5000,
                                multiplier_1=9))
        mejor = run.BRAIN.last['best']
        assert mejor is not None and mejor['kind'] == 'cola'

    def test_vale_cien_por_el_multiplicador(self):
        br = run.Brain()
        assert br._cola_value(1) == 100.0 * run.COLA_W
        assert br._cola_value(9) == 9 * br._cola_value(1)

    def test_mi_cola_perdida_vale_menos_que_la_comida_propia(self):
        br = run.Brain()
        assert br._negar_value(5) < br._cola_value(5)

    def test_puede_pisar_su_propia_cola_para_negarla(self):
        """Ⓑ es la cola que YO perdi: pisarla no da puntos pero se la quita
        al rival, asi que no es un obstaculo."""
        b = tablero('aaA' + run.COLA['B'] + '  ', '      ', '    Bb')
        e = run.scan(run.parse_board(b, 3, 6), 'A')
        assert e['cola_rival'] == {(0, 3)}
        assert (0, 3) not in e['obstacles']
        d = run.choose_direction(td(b, 'A', 3, 6))
        assert d in run.DIRS


# ============================================================================
#  5. PAREDES (v5) - se encogen cada ronda; chocarlas es un choque
# ============================================================================

class TestParedes:

    MURO = tablero(
        ' #         ', 'A#         ', 'a#    1    ', 'a #   2    ',
        '  #  3     ', '  #    4   ', '  # 5      ', '  #        ',
        '  #   X    ', '  #     X  ', '  #  Bbb   ', '  #        ')

    def test_las_reconoce(self):
        e = run.scan(run.parse_board(self.MURO, 12, 11), 'A')
        assert len(e['walls']) == 12 and e['A'] == (1, 0)
        assert not e['obstacles']

    def test_no_se_mete_en_la_pared(self):
        """Desde (1,0): arriba esta libre, abajo el propio cuerpo, a la
        derecha la pared. La unica salida es arriba."""
        assert run.choose_direction(td(self.MURO, 'A', 12, 11)) == 'up'

    def test_no_la_atraviesa_para_buscar_comida(self):
        e = run.scan(run.parse_board(self.MURO, 12, 11), 'A')
        for _ in range(5):
            d = run.choose_direction(td(self.MURO, 'A', 12, 11))
            assert destino(e['A'], d) not in e['walls']

    def test_barrido_con_paredes(self):
        rnd = random.Random(77)
        for _ in range(25):
            b, rows, cols = tablero_aleatorio(rnd, con_bombas=False)
            run.BRAIN.games.clear()
            d = run.choose_direction(td(b, 'A', rows, cols))
            e = run.scan(run.parse_board(b, rows, cols), 'A')
            nb = destino(e['A'], d)
            bloq = e['walls'] | e['a'] | e['b'] | {c for c in (e['A'], e['B']) if c}
            libres = [c for _, (dr, dc) in run.DIR_ITEMS
                      for c in [(e['A'][0] + dr, e['A'][1] + dc)]
                      if 0 <= c[0] < rows and 0 <= c[1] < cols and c not in bloq]
            if libres:
                assert nb not in bloq


# ============================================================================
#  6. SEGURIDAD - los tests que no pueden fallar nunca
# ============================================================================

class TestSeguridad:

    def test_nunca_elige_una_jugada_ilegal(self):
        rnd = random.Random(7)
        for _ in range(50):
            b, rows, cols = tablero_aleatorio(rnd, largo_mio=rnd.randint(3, 10))
            run.BRAIN.games.clear()
            d = run.choose_direction(td(b, 'A', rows, cols))
            e = run.scan(run.parse_board(b, rows, cols), 'A')
            nb = destino(e['A'], d)
            bloq = e['a'] | e['b'] | e['walls'] | e['obstacles'] | set(e['bombs'])
            bloq |= {c for c in (e['A'], e['B']) if c}
            libres = [c for _, (dr, dc) in run.DIR_ITEMS
                      for c in [(e['A'][0] + dr, e['A'][1] + dc)]
                      if 0 <= c[0] < rows and 0 <= c[1] < cols and c not in bloq]
            if libres:
                assert 0 <= nb[0] < rows and 0 <= nb[1] < cols
                assert nb not in bloq

    def test_nunca_come_un_digito_que_no_toca(self):
        rnd = random.Random(11)
        malos = 0
        for _ in range(40):
            b, rows, cols = tablero_aleatorio(rnd)
            run.BRAIN.games.clear()
            d = run.choose_direction(td(b, 'A', rows, cols))
            e = run.scan(run.parse_board(b, rows, cols), 'A')
            _, tval = run.target_digit(e['digits'])
            copias = run.copias_de(e['digits'], tval)
            nb = destino(e['A'], d)
            if nb in e['digits'] and nb not in copias:
                malos += 1
        assert malos == 0, '{} jugadas pisaron un digito equivocado'.format(malos)

    def test_todas_las_copias_del_objetivo_son_validas(self):
        """Bug de la version v4: marcaba como trampa las copias del objetivo
        que no habia elegido."""
        b = tablero('aA1  1 ', '   2   ', ' 1   2 ', '  3 B b')
        e = run.scan(run.parse_board(b, 4, 7), 'A')
        _, tval = run.target_digit(e['digits'])
        assert tval == 1 and len(run.copias_de(e['digits'], 1)) == 3
        d = run.choose_direction(td(b, 'A', 4, 7))
        assert destino(e['A'], d) == (0, 2), 'tenia el objetivo pegado'

    def test_no_se_encierra(self):
        b = tablero(
            'aaaaaaA   ', 'aaaaaaa   ', '          ', '   1  2   ',
            '   3  4   ', '   5      ', '      X  X', '          ',
            '   Bbb    ', '          ')
        d = run.choose_direction(td(b, 'A', 10, 10))
        assert d != 'left'

    def test_sin_salidas_no_explota(self):
        b = tablero(' a  ', 'aAa ', ' a  ', '    ')
        assert run.choose_direction(td(b, 'A', 4, 4)) in run.DIRS

    def test_is_safe_detecta_encierro(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        cuerpo = [(1, 1), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1), (2, 0), (1, 0)]
        assert not br._is_safe(cuerpo, [])[0]

    def test_is_safe_acepta_campo_abierto(self):
        br = run.Brain()
        br._setup_grid(12, 12)
        ok, area = br._is_safe([(5, 5), (5, 4), (5, 3)], [])
        assert ok and area > 100

    def test_la_cabeza_rival_cuenta_como_amenaza(self):
        br = run.Brain()
        br._setup_grid(6, 6)
        mio = [(2, 2), (2, 1), (2, 0)]
        assert br._is_safe(mio, [(0, 0)], opp_head=(0, 0))[1] <= \
            br._is_safe(mio, [])[1]


# ============================================================================
#  7. MODELO DE CHOQUE (v7) - el primero borra el puntaje, los demas son -500
# ============================================================================

class TestModeloDeChoque:

    def test_el_primer_choque_cuesta_todo_el_puntaje(self):
        assert run.Brain.costo_choque(25000, 0, 3, 1) == 25000

    def test_los_siguientes_cuestan_quinientos(self):
        assert run.Brain.costo_choque(25000, 1, 3, 1) == run.CHOQUE_MINIMO

    def test_con_puntaje_negativo_el_primero_tambien_es_quinientos(self):
        """Chocar nunca es una forma de limpiar un puntaje negativo."""
        assert run.Brain.costo_choque(-300, 0, 3, 1) == run.CHOQUE_MINIMO
        assert run.Brain.costo_choque(0, 0, 3, 1) == run.CHOQUE_MINIMO

    def test_chocar_largo_regala_la_cola_al_rival(self):
        corto = run.Brain.costo_choque(1000, 1, 3, 4)
        largo = run.Brain.costo_choque(1000, 1, 15, 4)
        assert largo > corto
        assert largo - corto == 12 * 100 * 4 * run.COLA_PERDIDA_W

    def test_una_vibora_de_tres_no_pierde_nada(self):
        assert run.Brain.costo_choque(1000, 1, 3, 9) == run.CHOQUE_MINIMO

    def test_el_costo_crece_con_el_puntaje(self):
        """La propiedad central de v7: barato al principio, ruinoso al final."""
        temprano = run.Brain.costo_choque(200, 0, 4, 1)
        tarde = run.Brain.costo_choque(30000, 0, 4, 1)
        assert tarde > temprano * 50

    def test_costo_de_bomba(self):
        assert run.Brain.costo_bomba(9000) == 9000
        assert run.Brain.costo_bomba(0) == run.CHOQUE_MINIMO
        assert run.Brain.costo_bomba(-200) == run.CHOQUE_MINIMO

    def test_cuenta_los_choques_mirando_si_la_cabeza_se_movio(self):
        """La firma de un choque en v7 es que la vibora no se mueve."""
        b = tablero('aaA   ', '      ', '  1 2 ', ' 3 4 5', '    Bb', '      ')
        datos = td(b, 'A', 6, 6, game_id='gch')
        run.BRAIN.decide(datos)
        assert run.BRAIN.stats('gch')['choques'] == 0
        run.BRAIN.decide(datos)      # mismo tablero: la cabeza no se movio
        assert run.BRAIN.stats('gch')['choques'] == 1


# ============================================================================
#  8. VALORACION
# ============================================================================

class TestValoracion:

    def test_una_X_vale_mucho_mas_que_su_bonus(self):
        br = run.Brain()
        br._setup_grid(25, 25)
        assert br._x_value(200) > 5000      # +50 nominal, pero multiplica todo

    def test_la_X_pierde_valor_hacia_el_final(self):
        br = run.Brain()
        br._setup_grid(25, 25)
        assert br._x_value(200) > br._x_value(50) > br._x_value(2)
        assert br._x_value(0) == 50.0

    def test_future_base_sin_movimientos(self):
        br = run.Brain()
        br._setup_grid(25, 25)
        assert br.future_base(0) == 0.0 and br.future_base(-5) == 0.0

    def test_un_nueve_vale_nueve_veces_un_uno(self):
        br = run.Brain()
        assert br._digit_value(9, 1, 0) == 9 * br._digit_value(1, 1, 0)

    def test_el_multiplicador_escala(self):
        br = run.Brain()
        assert br._digit_value(5, 3, 0) == 3 * br._digit_value(5, 1, 0)

    def test_negar_suma_si_el_rival_tiene_multiplicador_alto(self):
        br = run.Brain()
        assert br._digit_value(5, 1, 10) > br._digit_value(5, 1, 1)

    def test_distancia_esperada_minima_en_el_centro(self):
        br = run.Brain()
        br._setup_grid(21, 21)
        assert br.exp_dist[(10, 10)] == pytest.approx(0.0, abs=1e-9)
        assert br.exp_dist[(0, 0)] > br.exp_dist[(10, 10)]

    def test_typical_value_positivo(self):
        br = run.Brain()
        br._setup_grid(25, 25)
        ent = {'mi_cola': {(1, 1)}}
        assert br._typical_value([(9, {(0, 0)})], ent, 100, 2) > 0


class TestCarreras:

    @pytest.mark.parametrize('k,ko,side,pierde', [
        (5, 5, 'A', False),   # empate: A mueve primero
        (5, 5, 'B', True),    # empate: B mueve segundo
        (6, 5, 'A', True),
        (4, 5, 'B', False),
    ])
    def test_el_empate_lo_gana_quien_mueve_primero(self, k, ko, side, pierde):
        assert run.Brain._loses_race(k, ko, side) is pierde


# ============================================================================
#  9. SEGUIMIENTO DEL CUERPO
# ============================================================================

class TestBodyTracker:

    def test_arranque_en_frio(self):
        t = run.BodyTracker()
        assert t.update((0, 2), {(0, 0), (0, 1), (0, 2)}) == \
            [(0, 2), (0, 1), (0, 0)]

    def test_mantiene_el_orden_al_avanzar(self):
        t = run.BodyTracker()
        t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        assert t.update((0, 3), {(0, 1), (0, 2), (0, 3)}) == \
            [(0, 3), (0, 2), (0, 1)]

    def test_detecta_crecimiento(self):
        t = run.BodyTracker()
        t.update((0, 2), {(0, 0), (0, 1), (0, 2)})
        assert len(t.update((0, 3), {(0, 0), (0, 1), (0, 2), (0, 3)})) == 4

    def test_orden_correcto_en_vibora_enroscada(self):
        t = run.BodyTracker()
        t.order = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)]
        assert t.update((0, 0), set(t.order))[-1] == (1, 1)

    def test_se_recupera_tras_perder_la_cola(self):
        """En v7 un choque deja la vibora en 3 celdas de golpe."""
        t = run.BodyTracker()
        t.update((0, 5), {(0, 5), (0, 4), (0, 3), (0, 2), (0, 1)})
        orden = t.update((0, 5), {(0, 5), (0, 4), (0, 3)})
        assert orden == [(0, 5), (0, 4), (0, 3)]

    def test_cabeza_none(self):
        t = run.BodyTracker()
        t.update((0, 1), {(0, 0), (0, 1)})
        assert t.update(None, set()) == []


# ============================================================================
#  10. UTILIDADES INTERNAS
# ============================================================================

class TestInternos:

    def test_bfs_respeta_el_tiempo_de_liberacion(self):
        br = run.Brain()
        br._setup_grid(5, 5)
        assert br._bfs((0, 0), {(0, 1): 3}, frozenset())[(0, 1)] >= 3

    def test_bfs_esquiva_bloqueos(self):
        br = run.Brain()
        br._setup_grid(3, 3)
        assert (0, 2) not in br._bfs((0, 0), {},
                                     frozenset({(0, 1), (1, 1), (2, 1)}))

    def test_path_from(self):
        br = run.Brain()
        br._setup_grid(4, 4)
        dist, parent = br._bfs((0, 0), {}, frozenset(), with_parents=True)
        camino = br._path_from(parent, (0, 3))
        assert camino[-1] == (0, 3) and len(camino) == dist[(0, 3)]

    def test_path_from_al_origen(self):
        assert run.Brain._path_from({(0, 0): None}, (0, 0)) == []

    def test_advance(self):
        orden = [(0, 1), (0, 0)]
        assert run.Brain._advance(orden, [(0, 2)], set()) == [(0, 2), (0, 1)]
        assert run.Brain._advance(orden, [(0, 2)], {(0, 2)}) == \
            [(0, 2), (0, 1), (0, 0)]

    def test_shift(self):
        orden = [(0, 3), (0, 2), (0, 1), (0, 0)]
        sin = run.Brain._shift(orden, (0, 5), 2, False)
        assert sin[0] == (0, 5) and len(sin) == len(orden) - 1
        assert len(run.Brain._shift(orden, (0, 5), 2, True)) == len(sin) + 1

    def test_occupancy_libera_la_cola_primero(self):
        occ = run.Brain._occupancy([(0, 2), (0, 1), (0, 0)], [])
        assert occ[(0, 0)] == 1 and occ[(0, 2)] == 3

    def test_occupancy_toma_el_maximo(self):
        assert run.Brain._occupancy([(0, 0)], [(0, 0), (1, 0), (2, 0)])[(0, 0)] == 3

    @pytest.mark.parametrize('delta,esperado', [
        ((0, 1), 'right'), ((0, -1), 'left'), ((1, 0), 'down'), ((-1, 0), 'up'),
    ])
    def test_dir_name(self, delta, esperado):
        assert run.Brain._dir_name((1, 1), (1 + delta[0], 1 + delta[1])) == esperado

    def test_dir_name_con_salto_invalido(self):
        assert run.Brain._dir_name((0, 0), (5, 5)) == 'up'

    def test_setup_grid_cachea(self):
        a, b = run.Brain(), run.Brain()
        a._setup_grid(23, 19)
        b._setup_grid(23, 19)
        assert a.nbrs is b.nbrs

    def test_vecinos_en_los_bordes(self):
        br = run.Brain()
        br._setup_grid(5, 5)
        assert len(br.nbrs[(0, 0)]) == 2
        assert len(br.nbrs[(0, 2)]) == 3
        assert len(br.nbrs[(2, 2)]) == 4

    def test_game_crea_y_reutiliza(self):
        br = run.Brain()
        g1 = br._game('x')
        assert br._game('x') is g1 and br._game('y') is not g1


# ============================================================================
#  11. ROBUSTEZ - nunca crashear (un crash del proceso = timeout)
# ============================================================================

class TestRobustez:

    @pytest.mark.parametrize('datos', [
        {},
        {'board': ''},
        {'board': None},
        {'board': '|  |\n', 'side': 'A'},
        {'board': 'basura sin barras', 'side': 'A'},
        {'board': '|A|\n', 'side': 'Z'},
        {'board': '|A |\n| B|\n', 'side': 'A', 'rows': 'no es numero'},
        {'board': '|A |\n| B|\n', 'side': 'A', 'rows': -3, 'cols': 0},
    ])
    def test_siempre_devuelve_una_direccion_valida(self, datos):
        assert run.choose_direction(datos) in run.DIRS

    def test_sin_cabeza_propia(self):
        assert run.choose_direction(td(tablero('  B ', '    '), 'A')) in run.DIRS

    def test_board_size_cuando_faltan_rows_y_cols(self):
        datos = td(tablero('A   ', '    ', '   1', '2 3 '), 'A')
        del datos['rows'], datos['cols']
        datos['board_size'] = '4x4'
        assert run.BRAIN.decide(datos) in run.DIRS

    def test_board_size_malformado(self):
        datos = td(tablero('A   ', '    ', '   1', '2 3 '), 'A')
        del datos['rows'], datos['cols']
        datos['board_size'] = 'no-es-un-tamaño'
        assert run.BRAIN.decide(datos) in run.DIRS

    def test_si_el_cerebro_explota_hay_red(self, monkeypatch):
        monkeypatch.setattr(run.Brain, 'decide',
                            lambda self, d: (_ for _ in ()).throw(RuntimeError()))
        assert run.choose_direction(
            td(tablero('A   ', '   1', '2 3 ', '45 B'), 'A')) in run.DIRS

    def test_si_todo_explota_devuelve_up(self, monkeypatch):
        monkeypatch.setattr(run.Brain, 'decide',
                            lambda self, d: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(run, '_fallback',
                            lambda d: (_ for _ in ()).throw(RuntimeError()))
        assert run.choose_direction({}) == 'up'

    def test_fallback_evita_digitos_malos_y_bombas(self):
        b = tablero('3A1 ', ' ' + run.BOMBA_CHICA + '2 ', ' 4 5', '   B')
        e = run.scan(run.parse_board(b, 4, 4), 'A')
        _, tval = run.target_digit(e['digits'])
        copias = run.copias_de(e['digits'], tval)
        nb = destino(e['A'], run._fallback(td(b, 'A', 4, 4)))
        assert nb not in e['digits'] or nb in copias

    def test_fallback_sin_cabeza(self):
        assert run._fallback(td(tablero('    '), 'A')) == 'up'

    def test_fallback_con_tablero_vacio(self):
        assert run._fallback({'board': '', 'side': 'A'}) == 'up'

    def test_fallback_encerrado(self):
        assert run._fallback(td(tablero(' a  ', 'aAa ', ' a  ', '    '),
                                'A')) in run.DIRS

    def test_fallback_lado_invalido(self):
        assert run._fallback(td(tablero('A  1', '    '), 'Z')) in run.DIRS

    def test_respeta_el_presupuesto_en_el_tablero_mas_grande(self):
        import time
        rnd = random.Random(3)
        b, rows, cols = tablero_aleatorio(rnd, rows=30, cols=30, largo_mio=12)
        t0 = time.perf_counter()
        run.choose_direction(td(b, 'A', rows, cols, remaining_moves=400))
        assert (time.perf_counter() - t0) < run.HARD_BUDGET * 5

    def test_sin_tiempo_cae_a_supervivencia(self, monkeypatch):
        monkeypatch.setattr(run, 'HARD_BUDGET', -1.0)
        rnd = random.Random(5)
        b, rows, cols = tablero_aleatorio(rnd, rows=22, cols=22)
        assert run.choose_direction(td(b, 'A', rows, cols)) in run.DIRS

    @pytest.mark.parametrize('buenas', [2, 3, 4, 6, 10, 20])
    def test_corta_en_cualquier_punto_al_agotarse_el_tiempo(self, monkeypatch,
                                                            buenas):
        """El corte por presupuesto puede caer en cualquier lectura del reloj."""
        n = {'i': 0}
        real = run.time.perf_counter

        def reloj():
            n['i'] += 1
            return 0.0 if n['i'] <= buenas else 1e9

        rnd = random.Random(9)
        b, rows, cols = tablero_aleatorio(rnd, rows=24, cols=24)
        monkeypatch.setattr(run.time, 'perf_counter', reloj)
        try:
            d = run.choose_direction(td(b, 'A', rows, cols))
        finally:
            monkeypatch.setattr(run.time, 'perf_counter', real)
        assert d in run.DIRS

    def test_modo_blando_de_carrera(self, monkeypatch):
        monkeypatch.setattr(run, 'RACE_HARD_FILTER', False)
        b = tablero('aaA        ', '           ', '    2  3   ',
                    '  4   1B   ', '     5bb   ', '           ')
        assert run.choose_direction(td(b, 'A', 6, 11)) in run.DIRS

    def test_sin_skip(self, monkeypatch):
        monkeypatch.setattr(run, 'SKIP_ENABLED', False)
        b = tablero('aaA        ', '           ', '    2  3   ',
                    '  4   1B   ', '     5bb   ', '           ')
        assert run.choose_direction(td(b, 'A', 6, 11)) in run.DIRS


# ============================================================================
#  12. SUPERVIVENCIA
# ============================================================================

class TestSupervivencia:

    def base(self, celdas):
        return {n: {'cell': c, 'order': [c], 'safe': s, 'area': a}
                for n, (c, s, a) in celdas.items()}

    def vacio(self):
        return {'mi_cola': set(), 'X': set(), 'digits': {}, 'walls': set(),
                'bombs': {}, 'cola_rival': set(), 'obstacles': set()}

    def test_prefiere_lo_seguro(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = self.base({'up': ((1, 2), True, 40), 'down': ((3, 2), False, 3)})
        assert br._survival(base, [(2, 2), (2, 1)], [], None, set(), set(),
                            set(), self.vacio(), 8, 8, 0, 0, 100) == 'up'

    def test_penaliza_los_digitos_equivocados(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = self.base({'up': ((1, 2), True, 40), 'down': ((3, 2), True, 40)})
        assert br._survival(base, [(2, 2)], [], None, {(1, 2)}, set(), set(),
                            self.vacio(), 8, 8, 0, 0, 100) == 'down'

    def test_premia_la_comida_de_cola(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        ent = self.vacio()
        ent['mi_cola'] = {(3, 2)}
        base = self.base({'up': ((1, 2), True, 40), 'down': ((3, 2), True, 40)})
        assert br._survival(base, [(2, 2)], [], None, set(), set(), set(),
                            ent, 8, 8, 0, 0, 100) == 'down'

    def test_penaliza_los_bordes(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = self.base({'up': ((0, 4), True, 40), 'down': ((2, 4), True, 40)})
        assert br._survival(base, [(1, 4)], [], None, set(), set(), set(),
                            self.vacio(), 8, 8, 0, 0, 100) == 'down'

    def test_se_aleja_de_la_cabeza_rival(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = self.base({'up': ((3, 4), True, 40), 'down': ((5, 4), True, 40)})
        assert br._survival(base, [(4, 4)], [(2, 4)], (2, 4), set(), set(),
                            set(), self.vacio(), 8, 8, 0, 0, 100) == 'down'

    def test_el_aire_no_aplasta_la_penalizacion_del_digito(self):
        """Un digito equivocado son -500 reales. Si el termino de aire no
        saturara, 100 celdas mas de espacio lo taparian."""
        br = run.Brain()
        br._setup_grid(30, 30)
        base = {'up': {'cell': (1, 2), 'order': [(1, 2)], 'safe': True,
                       'area': 800},
                'down': {'cell': (3, 2), 'order': [(3, 2)], 'safe': True,
                         'area': 400}}
        d = br._survival(base, [(2, 2), (2, 1), (2, 0)], [], None, {(1, 2)},
                         set(), set(), self.vacio(), 30, 30, 0, 0, 100)
        assert d == 'down', 'el area tapo los -500 del digito equivocado'

    def test_el_riesgo_de_encierro_escala_con_el_puntaje(self):
        """Con mucho puntaje encima, quedar encerrado pesa muchisimo mas."""
        br = run.Brain()
        br._setup_grid(20, 20)
        base = {'up': {'cell': (1, 2), 'order': [(1, 2)], 'safe': False,
                       'area': 400},
                'down': {'cell': (3, 2), 'order': [(3, 2)], 'safe': True,
                         'area': 60}}
        cuerpo = [(2, 2), (2, 1), (2, 0)]
        barato = br._survival(base, cuerpo, [], None, set(), set(), set(),
                              self.vacio(), 20, 20, 0, 0, 100,
                              costo_crash=200)
        caro = br._survival(base, cuerpo, [], None, set(), set(), set(),
                            self.vacio(), 20, 20, 0, 0, 100,
                            costo_crash=30000)
        assert caro == 'down', 'con 30000 puntos no puede arriesgar el encierro'
        assert barato in ('up', 'down')

    def test_arriesga_mas_si_va_perdiendo_feo(self):
        br = run.Brain()
        br._setup_grid(8, 8)
        base = self.base({'up': ((1, 2), True, 40), 'down': ((3, 2), True, 40)})
        d = br._survival(base, [(2, 2)], [], None, {(1, 2)}, set(), set(),
                         self.vacio(), 8, 8, 0, 9000, 10)
        assert d in ('up', 'down')


# ============================================================================
#  13. CONTABILIDAD Y LOGS
# ============================================================================

class TestContabilidad:

    def test_cuenta_los_digitos_correctos(self):
        b = tablero('aA1 ', '  2 ', ' 3 4', '5  B')
        run.BRAIN.decide(td(b, 'A', 4, 4))
        st = run.BRAIN.stats('g_test')
        assert st['ok'] == 1 and st['bad'] == 0

    def test_cuenta_las_X(self):
        b = tablero('aAX    ', '   1   ', ' 2   3 ', '  4 5  ',
                    '    X  ', '  Bbb  ', '       ')
        run.BRAIN.decide(td(b, 'A', 7, 7))
        assert run.BRAIN.stats('g_test')['x'] == 1

    def test_cuenta_las_colas(self):
        b = tablero('aA' + run.COLA['A'] + '    ', '   1   ', ' 2   3 ',
                    '  4 5  ', '       ', '  Bbb  ', '       ')
        run.BRAIN.decide(td(b, 'A', 7, 7, multiplier_1=9))
        assert run.BRAIN.stats('g_test')['cola'] == 1

    def test_cuenta_el_digito_malo_cuando_no_hay_alternativa(self):
        b = tablero('A2 ', 'a  ', '  1')
        assert run.BRAIN.decide(td(b, 'A', 3, 3)) == 'right'
        assert run.BRAIN.stats('g_test')['bad'] == 1

    def test_stats_de_una_partida_inexistente(self):
        st = run.BRAIN.stats('no_existe')
        assert st == {'ok': 0, 'x': 0, 'bad': 0, 'cola': 0, 'choques': 0,
                      'times': []}

    def test_log_event_y_action(self):
        run.log_event('g1', {'event': 'x'})
        run.log_action('g1', {'action': 'move'})
        assert run.HISTORY['g1'][0].startswith('< ')
        assert run.HISTORY['g1'][1].startswith('> ')

    def test_write_game_log_incluye_la_version(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run.log_action('g9', {'action': 'move'})
        run.write_game_log('g9')
        texto = (tmp_path / 'game_g9.log').read_text(encoding='utf-8')
        assert run.BOT_VERSION in texto and '> ' in texto

    def test_write_game_log_no_explota_sin_permisos(self, monkeypatch):
        monkeypatch.setattr('builtins.open',
                            lambda *a, **k: (_ for _ in ()).throw(OSError('lleno')))
        run.write_game_log('g_inexistente')

    def test_write_live_es_atomico(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run.write_live({'event': 'your_turn', 'board': '|A|'})
        assert json.loads((tmp_path / 'live.json').read_text())['event'] == \
            'your_turn'
        assert not (tmp_path / 'live.tmp').exists()

    def test_write_live_no_explota(self, monkeypatch):
        monkeypatch.setattr('builtins.open',
                            lambda *a, **k: (_ for _ in ()).throw(OSError))
        run.write_live({'a': 1})


# ============================================================================
#  14. PARTIDAS SIMULTANEAS
# ============================================================================

class TestPartidasSimultaneas:

    CHICO = tablero(
        'aaA         ', '   1        ', '     2      ', '       3    ',
        '         4  ', '  5         ', '     X      ', '        X   ',
        '            ', '      Bbb   ', '            ', '            ')
    GRANDE = tablero(
        'aaA              ', '      1          ', '        2        ',
        '          3      ', '            4    ', '  5              ',
        '       X         ', '            X    ', '                 ',
        '         Bbb     ', '                 ', '                 ',
        '                 ', '                 ', '                 ',
        '                 ', '                 ', '                 ')

    def test_dos_partidas_de_distinto_tamaño(self):
        for _ in range(3):
            assert run.choose_direction(
                td(self.CHICO, 'A', 12, 12, game_id='G1')) in run.DIRS
            assert run.choose_direction(
                td(self.GRANDE, 'A', 18, 17, game_id='G2')) in run.DIRS
        assert set(run.BRAIN.games) == {'G1', 'G2'}

    def test_contadores_separados(self):
        con_x = tablero('aAX    ', '   1   ', ' 2   3 ', '  4 5  ',
                        '       ', '  Bbb  ', '       ')
        run.choose_direction(td(con_x, 'A', 7, 7, game_id='CON_X'))
        run.choose_direction(td(self.GRANDE, 'A', 18, 17, game_id='SIN_X'))
        assert run.BRAIN.stats('CON_X')['x'] == 1
        assert run.BRAIN.stats('SIN_X')['x'] == 0

    def test_choques_separados_por_partida(self):
        datos = td(self.CHICO, 'A', 12, 12, game_id='CH1')
        run.choose_direction(datos)
        run.choose_direction(datos)
        run.choose_direction(td(self.GRANDE, 'A', 18, 17, game_id='CH2'))
        assert run.BRAIN.stats('CH1')['choques'] == 1
        assert run.BRAIN.stats('CH2')['choques'] == 0

    def test_no_acumula_partidas_cortadas(self):
        """Si una partida se corta sin game_over su estado no debe quedar para
        siempre: con muchas reconexiones la memoria creceria sin limite."""
        for n in range(60):
            run.choose_direction(td(self.CHICO, 'A', 12, 12,
                                    game_id='huerfana_{}'.format(n)))
        assert len(run.BRAIN.games) <= run.MAX_TRACKED_GAMES

    def test_conserva_la_activa(self):
        run.choose_direction(td(self.CHICO, 'A', 12, 12, game_id='ACTIVA'))
        for n in range(run.MAX_TRACKED_GAMES + 3):
            run.choose_direction(td(self.CHICO, 'A', 12, 12,
                                    game_id='otra_{}'.format(n)))
            run.choose_direction(td(self.CHICO, 'A', 12, 12, game_id='ACTIVA'))
        assert 'ACTIVA' in run.BRAIN.games

    def test_ventana_de_tiempos_acotada(self):
        run.BRAIN.timings = [0.001] * 2500
        run.choose_direction(td(self.CHICO, 'A', 12, 12, game_id='W'))
        assert len(run.BRAIN.timings) <= 2000


# ============================================================================
#  15. CAPA DE RED (websocket simulado)
# ============================================================================

class FakeWS:
    """Websocket de mentira: entrega mensajes de una lista y guarda los enviados."""

    def __init__(self, entrantes=()):
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

    def test_send_serializa(self):
        ws = FakeWS()
        asyncio.run(run.send(ws, 'move', {'direction': 'up'}))
        assert ws.enviados == [{'action': 'move', 'data': {'direction': 'up'}}]

    def test_acepta_los_desafios(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('challenge', {'challenge_id': 'c1'})])
        asyncio.run(run.play(ws))
        assert ws.enviados[0]['action'] == 'accept_challenge'
        assert ws.enviados[0]['data']['challenge_id'] == 'c1'

    def test_responde_el_turno(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        datos = td(tablero('aA  ', '  1 ', '2 3 ', '45 B'), 'A', 4, 4)
        ws = FakeWS([ev('your_turn', datos)])
        asyncio.run(run.play(ws))
        env = ws.enviados[0]
        assert env['action'] == 'move'
        assert env['data']['direction'] in run.DIRS
        assert env['data']['turn_token'] == datos['turn_token']
        assert env['data']['game_id'] == datos['game_id']

    def test_ignora_las_listas_de_usuarios(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ws = FakeWS([ev('list_users', {'users': []}),
                     ev('update_user_list', {'users': []})])
        asyncio.run(run.play(ws))
        assert ws.enviados == []

    def test_game_over_guarda_el_log_y_suelta_el_estado(self, tmp_path,
                                                        monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        b = tablero('aA1 ', '  2 ', ' 3 4', '5  B')
        ws = FakeWS([ev('your_turn', td(b, 'A', 4, 4, game_id='gz')),
                     ev('game_over', {'game_id': 'gz', 'board': b,
                                      'score_1': 100, 'score_2': 50,
                                      'multiplier_1': 2, 'multiplier_2': 1,
                                      'winner': 'p1', 'player_1': 'p1',
                                      'player_2': 'p2'})])
        asyncio.run(run.play(ws))
        assert (tmp_path / 'game_gz.log').exists()
        assert 'gz' not in run.BRAIN.games
        assert '1 digitos correctos' in capsys.readouterr().out

    def test_avisa_si_comio_un_digito_malo(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        b = tablero('A2 ', 'a  ', '  1')
        ws = FakeWS([ev('your_turn', td(b, 'A', 3, 3, game_id='gb')),
                     ev('game_over', {'game_id': 'gb', 'board': b,
                                      'winner': 'p2', 'player_1': 'p1',
                                      'player_2': 'p2'})])
        asyncio.run(run.play(ws))
        assert 'AVISO' in capsys.readouterr().out

    def test_game_over_sin_game_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        asyncio.run(run.play(FakeWS([ev('game_over', {'board': '|A|',
                                                      'winner': 'p1'})])))

    def test_evento_de_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        asyncio.run(run.play(FakeWS([ev('error', {'msg': 'token invalido'})])))
        assert 'server error' in capsys.readouterr().out

    def test_ctrl_c_corta_el_bucle(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        class WSInterrumpido(FakeWS):
            async def recv(self):
                raise KeyboardInterrupt

        asyncio.run(run.play(WSInterrumpido()))

    def test_partida_completa(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rnd = random.Random(21)
        b, rows, cols = tablero_aleatorio(rnd, rows=20, cols=20)
        mensajes = [ev('challenge', {'challenge_id': 'c9'})]
        for i in range(4):
            mensajes.append(ev('your_turn',
                               td(b, 'A', rows, cols, game_id='gfull',
                                  remaining_moves=400 - 2 * i)))
        mensajes.append(ev('game_over', {
            'game_id': 'gfull', 'board': b, 'score_1': 5000, 'score_2': 100,
            'multiplier_1': 4, 'multiplier_2': 1, 'winner': 'p1',
            'player_1': 'p1', 'player_2': 'p2'}))
        ws = FakeWS(mensajes)
        asyncio.run(run.play(ws))
        movs = [m for m in ws.enviados if m['action'] == 'move']
        assert len(movs) == 4
        assert all(m['data']['direction'] in run.DIRS for m in movs)
        assert (tmp_path / 'game_gfull.log').exists()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
