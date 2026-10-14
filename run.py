#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 BOT DE SNAKE - codechallenge - reglas v8 (14 oct 2026)
 Uso:  python run.py <TU_TOKEN>
============================================================================

 MODELO DEL JUEGO

 Marcado [verificado] lo que esta comprobado contra logs reales de partidas
 y [enunciado] lo que sale solo de leer las reglas. Las reglas v5 a v8 son
 todas [enunciado]: todavia no hay logs con paredes, colas ni bombas.

 Tablero (una celda = UN caracter unicode; en Python list(fila) alcanza):
   ' '      vacio
   A/a B/b  cabeza y cuerpo de cada vibora
   1-9      comida numerada. Desde v6 cada digito aparece en 3-5 COPIAS.
   X        multiplicador
   #        pared: linea recta que se encoge hacia su centro cada ronda
   Ⓐ Ⓑ      comida de cola (U+24B6 / U+24B7): la letra dice QUIEN puede comerla
   💣 🧨     bombas de proximidad grande y chica

 Puntaje:
   * sobrevivir un movimiento            +1
   * digito correcto                     +digito * 100 * MI_multiplicador, crece
                                         y BORRA TODAS las copias de ese digito
   * digito equivocado                   -500 (no se multiplica), crece, reaparece
   * X                                   +50 fijo y +1 permanente al multiplicador
   * comida de cola propia (Ⓐ si soy A)  +100 * MI_multiplicador, crece
   * pisar MI cola perdida (Ⓑ si soy A)  0 puntos: solo se la niego al rival

 Secuencia de digitos [verificado 21/21]: los cinco valores en pantalla son
 consecutivos ciclicos y el objetivo es aquel cuyo predecesor ciclico NO esta.
 El enunciado avisa que NO es el mas chico: con 1 6 7 8 9 el objetivo es el 6
 y comerse ese 1 son -500.

 Choques [enunciado, v7]: chocar contra el borde, el propio cuerpo, una pared #
 o el rival YA NO termina la partida. La vibora se queda quieta, conserva 3
 celdas y el resto se vuelve comida para el rival. El PRIMER choque pone el
 score en 0; los siguientes son -500. Con score <= 0 el primero tambien es
 -500. Por debajo de -2500 se pierde.
   => El costo de chocar CRECE con el puntaje: barato al principio, ruinoso al
      final. Por eso la prudencia del bot escala con el score (ver costo_choque).

 Bombas [enunciado, v8]: explotan por CERCANIA, no por contacto. 💣 a distancia
 Chebyshev <= 2 (zona 5x5), 🧨 a <= 1 (zona 3x3). Explotar pone el score en 0
 (o -500 si ya era <= 0). No es un choque: el movimiento se hace igual.
 El enunciado garantiza que NUNCA hay nada comestible dentro de una zona, asi
 que esquivarlas no cuesta un solo punto: son pared dura y listo.
 Despues de la ronda 150 (quedan 100 movimientos) desaparecen todas.

 Partida: 400 movimientos (200 mios). Tablero de 20 a 30 por lado.
============================================================================
"""

import asyncio
import json
import os
import sys
import time
from collections import OrderedDict, deque

try:
    import websockets
except ImportError:          # pragma: no cover - permite importar sin la libreria
    websockets = None


BOT_VERSION = "v3.0-reglas-v8"   # si al arrancar no ves esto, corres un bot viejo


# ============================================================================
#  SIMBOLOS DEL TABLERO
#  Escritos como escapes a proposito: asi el archivo funciona aunque se guarde
#  con otra codificacion, y se ve exactamente que code point es cada uno.
# ============================================================================

COLA = {'A': 'Ⓐ', 'B': 'Ⓑ'}        # Ⓐ / Ⓑ comida de cola
BOMBA_GRANDE = '\U0001F4A3'                  # 💣 alcance 2 (zona 5x5)
BOMBA_CHICA = '\U0001F9E8'                   # 🧨 alcance 1 (zona 3x3)
PARED = '#'
MULTIPLICADOR = 'X'
ALCANCE = {BOMBA_GRANDE: 2, BOMBA_CHICA: 1}

DIRS = {
    'up': (-1, 0),
    'down': (1, 0),
    'left': (0, -1),
    'right': (0, 1),
}
DIR_ITEMS = tuple(DIRS.items())


# ============================================================================
#  CONSTANTES DE AJUSTE
#  Las de v4 venian calibradas con ~1500 partidas simuladas. Las nuevas son
#  estimaciones del enunciado hasta tener partidas reales con bombas y colas.
# ============================================================================

MOVE_BUDGET = 0.060       # presupuesto blando por jugada (s)
HARD_BUDGET = 0.090       # corte duro: devuelvo lo mejor que tenga

AVG_DIGIT_BASE = 500.0    # valor base medio de un digito (1..9 -> media 5 -> 500)
DIGIT_RATE = 0.075        # digitos que espero comer por movimiento propio

DENIAL_W = 0.20           # negarle el digito al rival (la secuencia es compartida)
LOST_RACE_DIGIT = 0.20    # si el rival llega antes, cuanto queda de su valor
LOST_RACE_X = 0.45
RACE_HARD_FILTER = True   # descarto el objetivo cuya carrera pierdo
SKIP_ENABLED = True       # dejarle el digito barato al rival y tomar el siguiente
SKIP_W = 0.90

COLA_W = 1.00             # peso de la comida de cola propia
NEGAR_W = 0.45            # peso de pisar mi cola perdida para negarsela al rival

BEAM_WIDTH = 5            # planes que sobreviven en cada nivel
MAX_LEGS = 4              # objetivos encadenados por plan
LEG_CONFIDENCE = 0.92     # descuento por cada tramo extra de la cadena
REPOS_W = 1.0             # peso del costo de reposicionamiento

SAFETY_MARGIN = 2         # casillas libres extra que exijo ademas de mi largo
SAFETY_OPP_STATIC = True  # al medir MI espacio, el cuerpo rival no se libera solo
MAX_PLAN_LEN = 40         # tableros de hasta 30x30: rutas mas largas que antes
MAX_TRACKED_GAMES = 8     # partidas cuyo estado mantengo a la vez

CHOQUE_MINIMO = 500.0     # lo que cuesta un choque que no es el primero
COLA_PERDIDA_W = 1.00     # peso de regalarle mi cola al rival al chocar
PISO_SCORE = -2500.0      # por debajo de esto se pierde la partida


# ============================================================================
#  LOGGING
# ============================================================================

HISTORY = {}


def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open("game_{}.log".format(game_id), "w", encoding="utf-8") as f:
            f.write("# bot_version: {}\n".format(BOT_VERSION))
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print("saved game_{}.log".format(game_id))
    except OSError as e:
        print("could not write game log: {}".format(e))


def write_live(data):
    """Estado actual a live.json (escritura atomica), por si hay un visor."""
    try:
        with open("live.tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace("live.tmp", "live.json")
    except OSError:
        pass


# ============================================================================
#  PARSEO DEL TABLERO
# ============================================================================

def parse_board(board, rows=None, cols=None):
    """String del tablero -> lista de listas de CARACTERES.

    Devuelve caracteres y no strings porque desde v8 el tablero trae 💣 y 🧨,
    que estan fuera del plano basico. En Python list(fila) ya los parte bien,
    pero trabajar con la lista explicita deja claro que una celda es un code
    point y evita cualquier indexado por bytes.

    rows/cols de turn_data mandan: si el servidor recorta los espacios del
    final, calcular el ancho con la fila mas larga achicaria el tablero y el
    bot creeria que hay pared donde no la hay.
    """
    if not board:
        return []
    crudas = [ln for ln in board.split('\n') if ln != '']
    filas = []
    for ln in crudas:
        if ln.startswith('|'):
            ln = ln[1:]
        if ln.endswith('|'):
            ln = ln[:-1]
        filas.append(list(ln))
    ancho = cols if cols else max((len(f) for f in filas), default=0)
    filas = [(f + [' '] * ancho)[:ancho] for f in filas]
    if rows:
        while len(filas) < rows:
            filas.append([' '] * ancho)
        filas = filas[:rows]
    return filas


def scan(grid, side='A'):
    """Extrae todas las entidades del tablero desde el punto de vista de 'side'."""
    yo = (side or 'A').upper()
    rival = 'B' if yo == 'A' else 'A'
    ent = {
        'A': None, 'B': None,
        'a': set(), 'b': set(),
        'digits': {},          # celda -> valor
        'X': set(),
        'walls': set(),        # #
        'bombs': {},           # celda -> alcance
        'mi_cola': set(),      # comida de cola que YO puedo comer
        'cola_rival': set(),   # mi cola perdida: pisarla se la niega al rival
        'obstacles': set(),    # cualquier cosa que no reconozco
    }
    mia = COLA[yo]
    suya = COLA[rival]
    for r, fila in enumerate(grid):
        for c, ch in enumerate(fila):
            if ch == ' ':
                continue
            if ch == 'A':
                ent['A'] = (r, c)
            elif ch == 'B':
                ent['B'] = (r, c)
            elif ch == 'a':
                ent['a'].add((r, c))
            elif ch == 'b':
                ent['b'].add((r, c))
            elif ch == MULTIPLICADOR:
                ent['X'].add((r, c))
            elif ch == PARED:
                ent['walls'].add((r, c))
            elif ch in ALCANCE:
                ent['bombs'][(r, c)] = ALCANCE[ch]
            elif ch == mia:
                ent['mi_cola'].add((r, c))
            elif ch == suya:
                ent['cola_rival'].add((r, c))
            elif '1' <= ch <= '9':
                ent['digits'][(r, c)] = ord(ch) - 48
            else:
                # Regla de oro: lo que no reconozco NO se pisa. Si el juego
                # agrega una entidad nueva el bot la esquiva en vez de meterse
                # adentro, que es la forma mas barata de morir por una regla
                # que todavia no implemente.
                ent['obstacles'].add((r, c))
    return ent


def zonas_de_explosion(bombs, rows, cols):
    """Celdas donde la CABEZA hace explotar una bomba.

    La distancia es Chebyshev (las diagonales cuentan como una celda), asi que
    la zona es un cuadrado: 5x5 alrededor de una 💣 y 3x3 alrededor de un 🧨.

    Como el enunciado garantiza que nunca hay nada comestible dentro de una
    zona, esquivarlas no cuesta puntos y se pueden tratar como pared dura.
    """
    zona = set()
    for (br, bc), alcance in bombs.items():
        for r in range(br - alcance, br + alcance + 1):
            for c in range(bc - alcance, bc + alcance + 1):
                if 0 <= r < rows and 0 <= c < cols:
                    zona.add((r, c))
    return zona


def target_digit(digits):
    """Objetivo: el digito cuyo predecesor ciclico NO esta en el tablero.

    Verificado 21/21 contra los digitos comidos en partidas reales. Funciona
    igual con copias multiples porque trabaja sobre el CONJUNTO de valores.

    El enunciado avisa que el objetivo no es el mas chico: con 1 6 7 8 9 en
    pantalla el proximo es el 6, y comerse ese 1 son -500.

    Devuelve (una celda cualquiera del objetivo, valor) o (None, None).
    """
    if not digits:
        return None, None
    presentes = set(digits.values())
    cands = [v for v in presentes if (9 if v == 1 else v - 1) not in presentes]
    if len(cands) == 1:
        val = cands[0]
    else:
        # Defensivo: nunca paso en 595 turnos reales.
        val = min(presentes)
    for celda, v in digits.items():
        if v == val:
            return celda, val
    return None, None


def copias_de(digits, valor):
    """Todas las celdas que tienen ese digito. Desde v6 son entre 3 y 5."""
    return {c for c, v in digits.items() if v == valor}


def secuencia_visible(digits):
    """Los cinco valores en pantalla, en orden ciclico desde el objetivo.

    Devuelve [(valor, {celdas}), ...]. Sirve para encadenar objetivos: cuando
    me como el actual ya se cual sigue y donde estan todas sus copias.
    """
    _, val = target_digit(digits)
    if val is None:
        return []
    presentes = set(digits.values())
    seq = []
    v = val
    for _ in range(len(presentes)):
        if v not in presentes:
            break
        seq.append((v, copias_de(digits, v)))
        v = 1 if v == 9 else v + 1
    return seq


# ============================================================================
#  SEGUIMIENTO DEL CUERPO (orden real cabeza -> cola)
# ============================================================================

class BodyTracker:
    """Mantiene el orden real del cuerpo entre turnos.

    El tablero dice QUE celdas ocupa cada vibora, no en que orden. Sin el orden
    no se sabe cual es la cola ni cuando se libera cada casilla.

    Entre dos turnos mios cada vibora avanzo un paso, asi que el orden nuevo es
    [cabeza] + (orden viejo filtrado por las celdas que siguen ocupadas). El
    conjunto observado es la verdad sobre QUIEN esta; el historial aporta el
    orden. Se autocorrige solo.
    """

    def __init__(self):
        self.order = []

    def update(self, head, cells):
        if head is None:
            self.order = []
            return self.order
        nuevo = [head]
        vistas = {head}
        for celda in self.order:
            if celda in cells and celda not in vistas:
                nuevo.append(celda)
                vistas.add(celda)
        if len(nuevo) != len(cells):
            # Arranque en frio, o la vibora perdio la cola por un choque.
            nuevo = self._walk(head, cells)
        self.order = nuevo
        return nuevo

    @staticmethod
    def _walk(head, cells):
        orden = [head]
        vistas = {head}
        cur = head
        while len(orden) < len(cells):
            sig = None
            for _, (dr, dc) in DIR_ITEMS:
                nb = (cur[0] + dr, cur[1] + dc)
                if nb in cells and nb not in vistas:
                    sig = nb
                    break
            if sig is None:
                break
            orden.append(sig)
            vistas.add(sig)
            cur = sig
        for celda in cells:
            if celda not in vistas:
                orden.append(celda)
        return orden


# ============================================================================
#  CEREBRO
# ============================================================================

class Brain:
    """Decide el movimiento. Un Brain por proceso; estado por game_id."""

    _grid_cache = {}

    def __init__(self):
        self.games = OrderedDict()
        self.timings = []
        self._bloqueo = frozenset()
        self._rate = DIGIT_RATE
        self.last = {}

    # ---------------- estado por partida ----------------

    def _game(self, game_id):
        g = self.games.get(game_id)
        if g is None:
            # Los contadores viven aca, por partida: si fueran del cerebro se
            # mezclarian entre partidas simultaneas.
            g = {'me': BodyTracker(), 'opp': BodyTracker(),
                 'ok': 0, 'x': 0, 'bad': 0, 'cola': 0,
                 'choques': 0, 'cabeza_previa': None, 'turnos': 0,
                 'times': []}
            self.games[game_id] = g
            while len(self.games) > MAX_TRACKED_GAMES:
                self.games.popitem(last=False)
        else:
            self.games.move_to_end(game_id)
        return g

    def stats(self, game_id):
        """Resumen de UNA partida."""
        g = self.games.get(game_id)
        if g is None:
            return {'ok': 0, 'x': 0, 'bad': 0, 'cola': 0, 'choques': 0,
                    'times': []}
        return {'ok': g['ok'], 'x': g['x'], 'bad': g['bad'], 'cola': g['cola'],
                'choques': g['choques'], 'times': list(g['times'])}

    # ---------------- utilidades de grilla ----------------

    def _setup_grid(self, rows, cols):
        self.rows = rows
        self.cols = cols
        cacheado = Brain._grid_cache.get((rows, cols))
        if cacheado is not None:
            self.nbrs, self.exp_dist = cacheado
            return
        nbrs = {}
        for r in range(rows):
            for c in range(cols):
                lst = []
                if r > 0:
                    lst.append((r - 1, c))
                if r < rows - 1:
                    lst.append((r + 1, c))
                if c > 0:
                    lst.append((r, c - 1))
                if c < cols - 1:
                    lst.append((r, c + 1))
                nbrs[(r, c)] = tuple(lst)

        # exp_dist[celda] = distancia Manhattan ESPERADA a una casilla al azar.
        # Los objetivos reaparecen en posiciones aleatorias, asi que terminar un
        # plan cerca del centro abarata todo lo que venga despues.
        def eje(n):
            return [(k * (k + 1) / 2.0 + (n - 1 - k) * (n - k) / 2.0) / n
                    for k in range(n)]
        er, ec = eje(rows), eje(cols)
        base = min(er) + min(ec)
        exp_dist = {(r, c): er[r] + ec[c] - base
                    for r in range(rows) for c in range(cols)}
        Brain._grid_cache[(rows, cols)] = (nbrs, exp_dist)
        self.nbrs, self.exp_dist = nbrs, exp_dist

    @staticmethod
    def _occupancy(my_order, opp_order):
        """celda -> instante a partir del cual queda libre.

        El segmento i de una vibora de largo L se libera dentro de L-i pasos.
        Entrar en el instante t es legal si t >= ese valor.
        """
        occ = {}
        L = len(my_order)
        for i, celda in enumerate(my_order):
            occ[celda] = L - i
        Lo = len(opp_order)
        for j, celda in enumerate(opp_order):
            # Para PLANIFICAR asumo que el rival avanza y libera casillas; para
            # medir si me encierro eso es peligrosamente optimista (ver _is_safe).
            v = Lo - j
            if v > occ.get(celda, 0):
                occ[celda] = v
        return occ

    def _bfs(self, start, occ, blocked, with_parents=False):
        """BFS respetando el instante en que se libera cada celda.

        Una celda todavia ocupada no se marca visitada, asi que puede
        alcanzarse mas tarde por un camino mas largo: es lo correcto.
        """
        dist = {start: 0}
        parent = {start: None} if with_parents else None
        q = deque((start,))
        nbrs = self.nbrs
        while q:
            cur = q.popleft()
            t = dist[cur] + 1
            for nb in nbrs[cur]:
                if nb in dist or nb in blocked:
                    continue
                if occ.get(nb, 0) > t:
                    continue
                dist[nb] = t
                if with_parents:
                    parent[nb] = cur
                q.append(nb)
        return (dist, parent) if with_parents else dist

    @staticmethod
    def _path_from(parent, celda):
        camino = []
        cur = celda
        while parent.get(cur) is not None:
            camino.append(cur)
            cur = parent[cur]
        camino.reverse()
        return camino

    @staticmethod
    def _advance(order, path, growth_cells):
        """Mueve mi vibora por el camino. Crece al pisar growth_cells."""
        cur = list(order)
        for celda in path:
            if celda in growth_cells:
                cur = [celda] + cur
            else:
                cur = [celda] + cur[:-1]
        return cur

    @staticmethod
    def _shift(order, celda, k, crecio):
        """Aproxima el cuerpo tras avanzar k pasos (solo para tramos profundos:
        no necesito la forma exacta, solo el largo y donde quedo la cabeza)."""
        keep = max(len(order) - k, 0)
        nuevo = [celda] + order[:keep]
        if crecio:
            nuevo.append(order[keep] if keep < len(order) else celda)
        return nuevo

    def _is_safe(self, my_order, opp_order, opp_head=None):
        """¿Llego a mi propia cola y me queda aire?

        Alcanzar la cola significa que puedo seguir girando indefinidamente: es
        el criterio clasico de no-encierro y el unico confiable.

        Dos correcciones que importan:
          - Mi cuerpo se libera un paso mas tarde de lo optimista, porque cada
            digito que como retrasa la cola.
          - El cuerpo del RIVAL no se libera solo: para planificar asumir que
            avanza esta bien, pero para medir si me encierro hace que la ruta
            de escape pase "a traves" de el.
        """
        head = my_order[0]
        L = len(my_order)
        occ = {}
        for i, celda in enumerate(my_order):
            occ[celda] = L - i + 1            # +1: margen por crecimiento
        Lo = len(opp_order)
        for j, celda in enumerate(opp_order):
            v = 10 ** 6 if SAFETY_OPP_STATIC else Lo - j
            if v > occ.get(celda, 0):
                occ[celda] = v
        if opp_head is not None:
            for nb in self.nbrs.get(opp_head, ()):
                if occ.get(nb, 0) < 2:
                    occ[nb] = 2
        alcance = self._bfs(head, occ, self._bloqueo)
        area = len(alcance)
        if area < L + SAFETY_MARGIN:
            return False, area
        return (my_order[-1] in alcance), area

    # ---------------- valoracion ----------------

    def future_base(self, moves_left):
        """Puntos base de digitos que espero cosechar en lo que queda.

        Es exactamente lo que agrega subir el multiplicador en +1, porque el
        multiplicador es aditivo: cada digito futuro suma una base mas.
        """
        if moves_left <= 0:
            return 0.0
        return AVG_DIGIT_BASE * self._rate * moves_left

    def _x_value(self, moves_left_on_arrival):
        return 50.0 + self.future_base(moves_left_on_arrival)

    def _digit_value(self, digit, my_mult, opp_mult):
        v = digit * 100.0 * my_mult
        # La secuencia es compartida: comerlo tambien se lo saca al rival.
        v += digit * 100.0 * opp_mult * DENIAL_W
        return v

    def _cola_value(self, my_mult):
        return 100.0 * my_mult * COLA_W

    def _negar_value(self, opp_mult):
        """Pisar mi cola perdida: no me da puntos, se los quita al rival."""
        return 100.0 * opp_mult * NEGAR_W

    @staticmethod
    def costo_choque(my_score, choques, largo, opp_mult):
        """Lo que me cuesta chocar AHORA.

        v7: el primer choque pone el score en 0 (o -500 si ya era <= 0) y los
        siguientes son -500. Ademas pierdo la cola: conservo 3 celdas y el
        resto se vuelve comida para el rival, que la cobra a 100 x SU
        multiplicador.

        De aca sale la propiedad mas importante del juego nuevo: chocar es
        barato cuando vas poco y ruinoso cuando vas ganando.
        """
        if choques == 0 and my_score > 0:
            directo = float(my_score)
        else:
            directo = CHOQUE_MINIMO
        regalo = max(largo - 3, 0) * 100.0 * opp_mult * COLA_PERDIDA_W
        return directo + regalo

    @staticmethod
    def costo_bomba(my_score):
        """Explotar pone el score en 0, o -500 si ya era <= 0.
        No es un choque: conservo la cola y el movimiento se hace igual."""
        return float(my_score) if my_score > 0 else CHOQUE_MINIMO

    @staticmethod
    def _loses_race(k, ko, side):
        """El juego es por turnos: A mueve antes que B en cada ronda."""
        return k > ko if side == 'A' else k >= ko

    # ---------------- decision principal ----------------

    def decide(self, td):
        t0 = time.perf_counter()
        deadline = t0 + MOVE_BUDGET
        hard = t0 + HARD_BUDGET

        side = (td.get('side') or 'A').upper()
        if side not in ('A', 'B'):
            side = 'A'
        opp_side = 'B' if side == 'A' else 'A'

        rows = td.get('rows')
        cols = td.get('cols')
        if not isinstance(rows, int) or not isinstance(cols, int) \
                or rows <= 0 or cols <= 0:
            rows = cols = None
            bs = td.get('board_size') or ''
            if 'x' in bs:
                try:
                    rows, cols = (int(x) for x in bs.split('x'))
                except ValueError:
                    rows = cols = None

        grid = parse_board(td.get('board', ''), rows, cols)
        if not grid:
            return 'up'
        rows = len(grid)
        cols = len(grid[0])
        self._setup_grid(rows, cols)

        ent = scan(grid, side)
        head = ent[side]
        opp_head = ent[opp_side]
        if head is None:
            return 'up'

        my_cells = set(ent[side.lower()])
        my_cells.add(head)
        opp_cells = set(ent[opp_side.lower()])
        if opp_head is not None:
            opp_cells.add(opp_head)

        g = self._game(td.get('game_id', '_'))
        # Detectar choques: si mi cabeza no se movio desde mi turno anterior,
        # es porque choque (es la firma de v7: "la vibora no se mueve").
        if g['turnos'] > 0 and g['cabeza_previa'] == head:
            g['choques'] += 1
        g['cabeza_previa'] = head
        g['turnos'] += 1

        my_order = g['me'].update(head, my_cells)
        opp_order = g['opp'].update(opp_head, opp_cells) if opp_head else []

        if side == 'A':
            my_mult = td.get('multiplier_1', 1) or 1
            opp_mult = td.get('multiplier_2', 1) or 1
            my_score = td.get('score_1', 0) or 0
            opp_score = td.get('score_2', 0) or 0
        else:
            my_mult = td.get('multiplier_2', 1) or 1
            opp_mult = td.get('multiplier_1', 1) or 1
            my_score = td.get('score_2', 0) or 0
            opp_score = td.get('score_1', 0) or 0

        rem = td.get('remaining_moves', 0) or 0
        my_moves = (rem + 1) // 2

        digits = ent['digits']
        seq = secuencia_visible(digits)
        tgt_val, tgt_copias = (seq[0] if seq else (None, set()))
        nxt_val, nxt_copias = (seq[1] if len(seq) > 1 else (None, set()))

        # Copias de OTROS digitos: -500 cada una. Las del objetivo NO son
        # trampas, son todas validas (esto era un bug en la version v4).
        wrong = {c for c, v in digits.items() if v != tgt_val}

        # Bloqueo duro para la cabeza: paredes, zonas de explosion y entidades
        # desconocidas. Las zonas salen gratis porque el enunciado garantiza
        # que nunca hay nada comestible adentro.
        zonas = zonas_de_explosion(ent['bombs'], rows, cols)
        bloqueo = ent['walls'] | zonas | ent['obstacles'] | set(ent['bombs'])
        self._bloqueo = frozenset(bloqueo)

        snake_cells = my_cells | opp_cells
        prohibido = snake_cells | bloqueo

        costo_crash = self.costo_choque(my_score, g['choques'],
                                        len(my_order), opp_mult)
        costo_boom = self.costo_bomba(my_score)

        # ---- movimientos por nivel de riesgo ----
        legal = []          # sin choque ni explosion
        riesgosos = []      # (costo, nombre, celda) para el caso desesperado
        for nombre, (dr, dc) in DIR_ITEMS:
            nb = (head[0] + dr, head[1] + dc)
            dentro = 0 <= nb[0] < rows and 0 <= nb[1] < cols
            if not dentro:
                riesgosos.append((costo_crash, nombre, nb))
                continue
            if nb in snake_cells or nb in ent['walls'] or nb in ent['obstacles'] \
                    or nb in ent['bombs']:
                riesgosos.append((costo_crash, nombre, nb))
            elif nb in zonas:
                riesgosos.append((costo_boom, nombre, nb))
            else:
                legal.append((nombre, nb))

        if not legal:
            # Nada seguro: elijo el mal menor en puntos, no al azar.
            riesgosos.sort(key=lambda x: x[0])
            direction = riesgosos[0][1] if riesgosos else 'up'
            self._registrar(g, head, direction, digits, tgt_copias, ent)
            self._anotar(g, t0, direction, 0, None, True, tgt_val, head,
                         [n for n, _ in legal], costo_crash, costo_boom)
            return direction

        # ---- evaluacion base de cada movimiento legal ----
        crece = set(tgt_copias) | wrong | ent['mi_cola']
        base = {}
        for nombre, celda in legal:
            order1 = self._advance(my_order, [celda], crece)
            seguro, area = self._is_safe(order1, opp_order, opp_head)
            base[nombre] = {'cell': celda, 'order': order1,
                            'safe': seguro, 'area': area}

        # ---- distancias del rival, para las carreras ----
        opp_dist = {}
        if opp_head is not None:
            opp_dist = self._bfs(opp_head,
                                 self._occupancy(opp_order, my_order),
                                 frozenset(wrong | bloqueo))

        # ---- busqueda en haz sobre cadenas de objetivos ----
        plans = self._beam(my_order, opp_order, seq, ent, my_moves,
                           my_mult, opp_mult, opp_dist, side, deadline)

        # ---- validar seguridad y elegir ----
        best = None
        for p in sorted(plans, key=lambda p: p['score'], reverse=True):
            if time.perf_counter() > hard:
                break      # sin tiempo: mejor caer a supervivencia que arriesgar
            # El plan puede APUNTAR a un digito que todavia no esta activo
            # (esperando que el rival avance la secuencia), pero jamas puede
            # PISARLO antes de tiempo: eso son -500 seguros.
            if p['first'] in wrong:
                continue
            b = base.get(self._dir_name(head, p['first']))
            if b is None or not b['safe']:
                continue
            if not self._is_safe(p['order1'], opp_order, opp_head)[0]:
                continue
            best = p
            break

        if best is not None:
            direction = self._dir_name(head, best['first'])
        else:
            direction = self._survival(base, my_order, opp_order, opp_head,
                                       wrong, tgt_copias, nxt_copias,
                                       ent, rows, cols, my_score, opp_score,
                                       my_moves, costo_crash, my_mult)

        self._registrar(g, head, direction, digits, tgt_copias, ent)
        self._anotar(g, t0, direction, len(plans), best, best is None,
                     tgt_val, head, [n for n, _ in legal],
                     costo_crash, costo_boom)
        return direction

    # ---------------- contabilidad y diagnostico ----------------

    @staticmethod
    def _registrar(g, head, direction, digits, tgt_copias, ent):
        """Cuenta que comi, para el resumen del final de la partida."""
        dr, dc = DIRS[direction]
        dest = (head[0] + dr, head[1] + dc)
        if dest in digits:
            if dest in tgt_copias:
                g['ok'] += 1
            else:
                g['bad'] += 1
        elif dest in ent['X']:
            g['x'] += 1
        elif dest in ent['mi_cola']:
            g['cola'] += 1

    def _anotar(self, g, t0, direction, n_planes, best, survival, tgt_val,
                head, legales, costo_crash, costo_boom):
        self.last = {'dir': direction, 'plans': n_planes, 'best': best,
                     'survival': survival, 'tval': tgt_val, 'head': head,
                     'legal': legales, 'costo_choque': costo_crash,
                     'costo_bomba': costo_boom, 'choques': g['choques']}
        transcurrido = time.perf_counter() - t0
        g['times'].append(transcurrido)
        self.timings.append(transcurrido)
        if len(self.timings) > 2000:        # ventana acotada: otra fuga menos
            del self.timings[:-2000]

    # ---------------- busqueda ----------------

    def _beam(self, my_order, opp_order, seq, ent, my_moves, my_mult,
              opp_mult, opp_dist, side, deadline):
        """Busqueda en haz sobre CADENAS de objetivos.

        Un plan no es "ir a lo mas cercano" sino una secuencia: comer el 6,
        despues el 7 que ya esta a la vista, despues una X. Como los cinco
        digitos del tablero son consecutivos y cada uno tiene varias copias,
        la secuencia completa es conocida de antemano.

        El puntaje de un plan es (puntos + bocado_tipico) / (movimientos +
        costo_de_reposicionamiento). El segundo termino importa: como el
        proximo objetivo aparecera en una casilla al azar, terminar cerca del
        centro vale movimientos reales mas adelante.
        """
        head = my_order[0]
        inicio = {
            'cell': head, 'order': my_order, 'order1': None, 'moves': 0,
            'value': 0.0, 'first': None, 'si': 0,
            'xs': frozenset(ent['X']), 'colas': frozenset(ent['mi_cola']),
            'score': -1e18, 'kind': None,
        }
        beam = [inicio]
        hechos = []
        tipico = self._typical_value(seq, ent, my_moves, my_mult)

        for leg in range(MAX_LEGS):
            siguientes = []
            for st in beam:
                if time.perf_counter() > deadline and leg > 0:
                    break
                presupuesto = my_moves - st['moves']
                if presupuesto <= 0:
                    continue

                activo = seq[st['si']] if st['si'] < len(seq) else None
                # Los digitos que todavia NO tocan son trampas de -500.
                peligros = set()
                for val, celdas in seq[st['si'] + 1:]:
                    peligros |= celdas
                occ = self._occupancy(st['order'], opp_order)
                quiere_padres = (st['first'] is None)
                res = self._bfs(st['cell'], occ,
                                frozenset(peligros | self._bloqueo),
                                with_parents=quiere_padres)
                dist, parent = res if quiere_padres else (res, None)

                objetivos = []
                if activo is not None:
                    # TODAS las copias del objetivo valen: voy a la mas cercana.
                    mejor = None
                    for celda in activo[1]:
                        d = dist.get(celda)
                        if d is not None and (mejor is None or d < mejor[1]):
                            mejor = (celda, d)
                    if mejor:
                        objetivos.append((mejor[0], 'digit', activo[0], mejor[1]))
                for x in st['xs']:
                    if x in dist:
                        objetivos.append((x, 'X', None, dist[x]))
                for celda in st['colas']:
                    if celda in dist:
                        objetivos.append((celda, 'cola', None, dist[celda]))
                if leg == 0:
                    for celda in ent['cola_rival']:
                        if celda in dist:
                            objetivos.append((celda, 'negar', None, dist[celda]))

                # ---- dejarle el digito barato al rival y tomar el de arriba ----
                # Un 1 a multiplicador 5 son 500 puntos por varios movimientos,
                # cuando el promedio ronda los 160 por movimiento. Si encima el
                # rival llega antes, conviene que EL queme el barato (la
                # secuencia avanza igual para los dos) y quedarme con el
                # siguiente, que vale hasta nueve veces mas.
                skip_parent = None
                if (SKIP_ENABLED and quiere_padres and activo is not None
                        and st['si'] + 1 < len(seq)):
                    ko_act = min((opp_dist[c] for c in activo[1]
                                  if c in opp_dist), default=None)
                    k_act = min((dist[c] for c in activo[1] if c in dist),
                                default=None)
                    if ko_act is not None and (
                            k_act is None or self._loses_race(k_act, ko_act, side)):
                        nval, ncopias = seq[st['si'] + 1]
                        libres = frozenset((peligros - ncopias) | self._bloqueo)
                        d2, skip_parent = self._bfs(st['cell'], occ, libres,
                                                    with_parents=True)
                        mejor = None
                        for celda in ncopias:
                            d = d2.get(celda)
                            if d is not None and (mejor is None or d < mejor[1]):
                                mejor = (celda, d)
                        if mejor:
                            # No es comestible hasta que el rival coma el activo.
                            objetivos.append((mejor[0], 'skip', nval,
                                              max(mejor[1], ko_act + 1)))

                for celda, kind, dval, k in objetivos:
                    if k <= 0 or k > presupuesto or k > MAX_PLAN_LEN:
                        continue

                    # ---- carrera contra el rival ----
                    # Comparo tiempos TOTALES desde ahora, no solo el primer
                    # tramo: si el rival pisa el objetivo antes, ir para alla es
                    # tirar movimientos. Como el juego es por turnos, el empate
                    # lo gana el que mueve primero en la ronda.
                    perdida = False
                    if kind != 'skip':
                        ko = opp_dist.get(celda)
                        perdida = (ko is not None and
                                   self._loses_race(st['moves'] + k, ko, side))

                    if kind == 'digit':
                        v = self._digit_value(dval, my_mult, opp_mult)
                        if perdida:
                            if RACE_HARD_FILTER:
                                continue
                            v *= LOST_RACE_DIGIT
                    elif kind == 'skip':
                        v = self._digit_value(dval, my_mult, opp_mult) * SKIP_W
                    elif kind == 'cola':
                        v = self._cola_value(my_mult)
                        if perdida:
                            continue        # la cola no se reparte: o llego o no
                    elif kind == 'negar':
                        v = self._negar_value(opp_mult)
                    else:
                        v = self._x_value(my_moves - st['moves'] - k)
                        if perdida:
                            if RACE_HARD_FILTER:
                                continue
                            v *= LOST_RACE_X
                    v *= LEG_CONFIDENCE ** leg
                    if v <= 0:
                        continue

                    if quiere_padres:
                        padres = skip_parent if kind == 'skip' else parent
                        camino = self._path_from(padres, celda)
                        if not camino:
                            continue
                        primero = camino[0]
                        # Lo que me hace crecer: digito (objetivo o skip) y cola.
                        crecer = {celda} if kind in ('digit', 'skip', 'cola') \
                            else frozenset()
                        order = self._advance(st['order'], camino, crecer)
                        order1 = order
                    else:
                        primero = st['first']
                        order = self._shift(st['order'], celda, k,
                                            kind in ('digit', 'skip', 'cola'))
                        order1 = st['order1']

                    ns = {
                        'cell': celda, 'order': order, 'order1': order1,
                        'moves': st['moves'] + k, 'value': st['value'] + v,
                        'first': primero, 'kind': kind,
                        'si': st['si'] + (2 if kind == 'skip'
                                          else 1 if kind == 'digit' else 0),
                        'xs': st['xs'] - {celda} if kind == 'X' else st['xs'],
                        'colas': st['colas'] - {celda} if kind == 'cola'
                                 else st['colas'],
                    }
                    ns['score'] = ((ns['value'] + tipico) /
                                   (ns['moves'] + REPOS_W * self.exp_dist[celda]))
                    siguientes.append(ns)

            if not siguientes:
                break
            siguientes.sort(key=lambda s: s['score'], reverse=True)
            hechos.extend(siguientes[:BEAM_WIDTH])
            beam = siguientes[:BEAM_WIDTH]
            if time.perf_counter() > deadline:
                break
        return hechos

    def _typical_value(self, seq, ent, my_moves, my_mult):
        """Valor del bocado 'promedio' disponible ahora, para que el costo de
        reposicionamiento tenga una escala con la que compararse."""
        vals = [self._x_value(my_moves)]
        if seq:
            vals.append(self._digit_value(seq[0][0], my_mult, 1))
        if ent['mi_cola']:
            vals.append(self._cola_value(my_mult))
        return sum(vals) / len(vals)

    # ---------------- supervivencia ----------------

    def _survival(self, base, my_order, opp_order, opp_head, wrong,
                  tgt_copias, nxt_copias, ent, rows, cols,
                  my_score, opp_score, my_moves,
                  costo_crash=CHOQUE_MINIMO, my_mult=1):
        """Ningun plan valido: elijo el movimiento que mas futuro me deja.

        El puntaje esta en PUNTOS DE LA PARTIDA, no en unidades inventadas: el
        riesgo de encierro vale lo que cuesta chocar ahora y un digito
        equivocado vale sus -500. El aire disponible satura a proposito: una
        vez que entro tres veces mi largo, mas espacio no cambia nada, y si no
        saturara aplastaria penalizaciones que si son puntos de verdad.
        """
        perdiendo_feo = (opp_score - my_score) > 3000 and my_moves < 30
        L = len(my_order)
        iman = None
        for conjunto in (tgt_copias, ent['mi_cola'], nxt_copias):
            if conjunto:
                iman = min(conjunto)
                break

        mejor_nombre, mejor_score = None, None
        for nombre, b in base.items():
            celda = b['cell']
            if b['safe']:
                s = 0.0
            else:
                # Sin cola alcanzable: cuanto me costaria terminar encerrado.
                ok2, _ = self._is_safe(b['order'], opp_order)
                s = -costo_crash * (0.25 if ok2 else 0.60)
            s += 250.0 * min(1.0, b['area'] / float(max(3 * L, 12)))
            if celda in wrong:
                s -= 500.0 * (0.3 if perdiendo_feo else 1.0)
            if celda in ent['mi_cola']:
                s += self._cola_value(my_mult)
            r, c = celda
            # Pegado a la pared pierdo la mitad de las salidas.
            if r == 0 or r == rows - 1:
                s -= 15.0
            if c == 0 or c == cols - 1:
                s -= 15.0
            if iman is not None:
                s -= 2.0 * (abs(celda[0] - iman[0]) + abs(celda[1] - iman[1]))
            if opp_head is not None:
                d = abs(celda[0] - opp_head[0]) + abs(celda[1] - opp_head[1])
                if d <= 1:
                    s -= 30.0
            if mejor_score is None or s > mejor_score:
                mejor_nombre, mejor_score = nombre, s
        return mejor_nombre or next(iter(base))

    @staticmethod
    def _dir_name(head, celda):
        dr = celda[0] - head[0]
        dc = celda[1] - head[1]
        for nombre, d in DIR_ITEMS:
            if d == (dr, dc):
                return nombre
        return 'up'


BRAIN = Brain()


def choose_direction(turn_data):
    """Punto de entrada del cerebro, con red de seguridad total."""
    try:
        return BRAIN.decide(turn_data)
    except Exception as e:                  # nunca crashear: un crash = timeout
        print('brain error: {}'.format(e))
        try:
            return _fallback(turn_data)
        except Exception:
            return 'up'


def _fallback(td):
    """Movimiento legal cualquiera, sin inteligencia, si el cerebro falla.

    Respeta igual lo que no se puede pisar: paredes, zonas de explosion y
    digitos equivocados.
    """
    side = (td.get('side') or 'A').upper()
    if side not in ('A', 'B'):
        side = 'A'
    grid = parse_board(td.get('board', ''), td.get('rows'), td.get('cols'))
    if not grid:
        return 'up'
    rows, cols = len(grid), len(grid[0])
    ent = scan(grid, side)
    head = ent[side]
    if head is None:
        return 'up'
    ocupadas = ent['a'] | ent['b'] | ent['walls'] | ent['obstacles'] \
        | set(ent['bombs'])
    if ent['A']:
        ocupadas.add(ent['A'])
    if ent['B']:
        ocupadas.add(ent['B'])
    zonas = zonas_de_explosion(ent['bombs'], rows, cols)
    _, tval = target_digit(ent['digits'])
    wrong = {c for c, v in ent['digits'].items() if v != tval}
    mejor = None
    for nombre, (dr, dc) in DIR_ITEMS:
        nb = (head[0] + dr, head[1] + dc)
        if not (0 <= nb[0] < rows and 0 <= nb[1] < cols) or nb in ocupadas:
            continue
        puntos = 0
        if nb in zonas:
            puntos -= 2
        if nb in wrong:
            puntos -= 1
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, nombre)
    return mejor[1] if mejor else 'up'


# ============================================================================
#  CONEXION AL SERVIDOR
# ============================================================================

async def send(websocket, action, data):
    await websocket.send(json.dumps({'action': action, 'data': data}))


async def process_your_turn(websocket, request_data):
    data = request_data['data']
    direction = choose_direction(data)
    move = {
        'game_id': data.get('game_id'),
        'turn_token': data.get('turn_token'),
        'direction': direction,
    }
    log_action(move['game_id'], {'action': 'move', 'data': move})
    await send(websocket, 'move', move)
    st = BRAIN.stats(data.get('game_id'))
    if st['times']:
        print('mv {} | {:<5} | {:.1f} ms'.format(
            data.get('remaining_moves'), direction, st['times'][-1] * 1000))


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            request_data = json.loads(request)
            event = request_data.get('event')

            if event in ('update_user_list', 'list_users'):
                pass

            elif event == 'challenge':
                await send(websocket, 'accept_challenge',
                           {'challenge_id': request_data['data']['challenge_id']})

            elif event == 'your_turn':
                d = request_data['data']
                log_event(d.get('game_id'), request_data)
                write_live({**d, 'event': 'your_turn'})
                await process_your_turn(websocket, request_data)

            elif event == 'game_over':
                d = request_data['data']
                write_live({**d, 'event': 'game_over'})
                game_id = d.get('game_id')
                st = BRAIN.stats(game_id)      # leer ANTES de soltar el estado
                if game_id:
                    log_event(game_id, request_data)
                    write_game_log(game_id)
                    BRAIN.games.pop(game_id, None)
                print('-' * 64)
                print('FIN | bot {} | {} ({} x{}) vs {} ({} x{}) | gano {}'.format(
                    BOT_VERSION,
                    d.get('player_1'), d.get('score_1'), d.get('multiplier_1'),
                    d.get('player_2'), d.get('score_2'), d.get('multiplier_2'),
                    d.get('winner')))
                print('comidos: {} digitos correctos, {} X, {} colas, '
                      '{} digitos ERRADOS | choques: {}'
                      .format(st['ok'], st['x'], st['cola'], st['bad'],
                              st['choques']))
                if st['bad']:
                    print('AVISO: cada digito equivocado son -500.')
                if st['times']:
                    ts = st['times']
                    print('tiempo por jugada: medio {:.1f} ms | maximo {:.1f} ms'
                          .format(1000 * sum(ts) / len(ts), 1000 * max(ts)))
                print('-' * 64)

            elif event == 'error':
                print('server error: {}'.format(request_data.get('data')))

        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            print('error {}'.format(e))
            break


async def start(auth_token):   # pragma: no cover
    uri = "wss://server.codechallenge.net.ar/ws?token={}".format(auth_token)
    print('=' * 64)
    print(' BOT SNAKE {} | reglas v8: digitos con copias, X, paredes,'.format(
        BOT_VERSION))
    print(' comida de cola y bombas de proximidad')
    print(' presupuesto por jugada: {:.0f} ms'.format(MOVE_BUDGET * 1000))
    print('=' * 64)
    while True:
        try:
            print('connecting to {}'.format(uri))
            async with websockets.connect(uri) as websocket:
                print('connection READY!')
                write_live({'board': '', 'event': 'waiting'})
                await play(websocket)
        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception:
            print('connection error!')
            time.sleep(3)


if __name__ == '__main__':   # pragma: no cover
    if len(sys.argv) >= 2:
        asyncio.run(start(sys.argv[1]))
    else:
        print('please provide your auth_token')
