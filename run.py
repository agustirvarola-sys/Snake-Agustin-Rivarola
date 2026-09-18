


import asyncio
import json
import os
import sys
import time
from collections import deque

try:
    import websockets
except ImportError:  # permite importar el cerebro sin la libreria (tests)
    websockets = None


BOT_VERSION = "v2.0-reglas-v4"   # <-- si al arrancar no ves esto, estas corriendo el bot VIEJO



MOVE_BUDGET = 0.060       # presupuesto blando por jugada (s) - el bot viejo usaba 0.10
HARD_BUDGET = 0.085       # corte duro: por encima de esto devuelvo lo mejor que tenga

AVG_DIGIT_BASE = 500.0    # valor base promedio de un digito (digitos 1..9 -> media 5 -> 500)
DIGIT_RATE = 0.070        # digitos por movimiento propio, si la adaptacion esta apagada
DIGIT_RATE_ADAPTIVE = False  # probado: no mejora. Interruptor disponible por si querés reprobarlo
DIGIT_RATE_K = 2.24       # tasa = K / (filas + columnas); calibrado en 16x16 -> 0.070

DENIAL_W = 0.20           # peso de negarle el digito al rival (secuencia compartida)
LOST_RACE_DIGIT = 0.20    # si el rival llega antes al digito, cuanto queda de su valor
LOST_RACE_X = 0.45        # idem para la X (menos grave: reaparece otra)
RACE_HARD_FILTER = True   # descarto el objetivo cuya carrera pierdo (tecnica del bot viejo)
SKIP_ENABLED = True       # dejarle el digito barato al rival y tomar el siguiente
SKIP_W = 0.90             # confianza en esa jugada de espera
WRONG_DIGIT_COST = 500.0  # penalizacion por pisar un digito equivocado

BEAM_WIDTH = 5            # planes que sobreviven en cada nivel de la busqueda
MAX_LEGS = 5              # objetivos encadenados (veo hasta 5 digitos por delante)
LEG_CONFIDENCE = 0.92     # confianza que le resto a cada tramo extra de la cadena
REPOS_W = 1.0             # peso del costo de reposicionamiento (ver exp_dist)

SAFETY_MARGIN = 2         # casillas libres extra que exijo ademas de mi largo
MAX_PLAN_LEN = 30         # no planifico rutas mas largas que esto

DIRS = {
    'up': (-1, 0),
    'down': (1, 0),
    'left': (0, -1),
    'right': (0, 1),
}
DIR_ITEMS = tuple(DIRS.items())


# ============================================================================
#  LOGGING / VISOR (igual que el cliente de referencia)
# ============================================================================

HISTORY = {}


def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open("game_{}.log".format(game_id), "w") as f:
            f.write("# bot_version: {}\n".format(BOT_VERSION))
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print("saved game_{}.log".format(game_id))
    except OSError as e:
        print("could not write game log: {}".format(e))


def write_live(data):
    """Estado actual a live.json (escritura atomica) para el visor en vivo."""
    try:
        with open("live.tmp", "w") as f:
            json.dump(data, f)
        os.replace("live.tmp", "live.json")
    except OSError:
        pass


# ============================================================================
#  PARSEO DEL TABLERO
# ============================================================================

def parse_board(board, rows=None, cols=None):
    """String del tablero -> lista de strings (una por fila), sin los '|'.

    Uso rows/cols de turn_data como fuente de verdad: si el servidor recorta
    espacios finales, rellenar al ancho de la fila mas larga achicaria el
    tablero y el bot creeria que hay pared donde no la hay.
    """
    raw = [ln for ln in board.split('\n') if ln != '']
    out = []
    for ln in raw:
        if ln.startswith('|'):
            ln = ln[1:]
        if ln.endswith('|'):
            ln = ln[:-1]
        out.append(ln)
    if cols:
        out = [ln.ljust(cols)[:cols] for ln in out]
    else:
        w = max((len(ln) for ln in out), default=0)
        out = [ln.ljust(w) for ln in out]
    if rows:
        while len(out) < rows:
            out.append(' ' * (cols or (len(out[0]) if out else 0)))
        out = out[:rows]
    return out


def scan(grid):
    """Extrae entidades del tablero."""
    ent = {
        'A': None, 'B': None,
        'a': set(), 'b': set(),
        'digits': {},          # celda -> valor
        'X': set(),
    }
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
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
            elif ch == 'X':
                ent['X'].add((r, c))
            elif '1' <= ch <= '9':
                ent['digits'][(r, c)] = ord(ch) - 48
    return ent


def target_digit(digits):
    """El objetivo es el digito cuyo predecesor ciclico no esta en el tablero.

    Verificado contra los 21 digitos comidos en los logs: 21/21 correcto.
    Devuelve (celda, valor) o (None, None).
    """
    if not digits:
        return None, None
    present = set(digits.values())
    cands = [v for v in present if (9 if v == 1 else v - 1) not in present]
    if len(cands) == 1:
        val = cands[0]
    else:
        # Fallback defensivo (nunca paso en 453 turnos analizados):
        # tomo el menor, que es el arranque natural de la secuencia.
        val = min(present)
    for cell, v in digits.items():
        if v == val:
            return cell, val
    return None, None


# ============================================================================
#  SEGUIMIENTO DEL CUERPO (orden real cabeza -> cola)
# ============================================================================

class BodyTracker:
    """Mantiene el orden real del cuerpo entre turnos.

    El tablero solo dice QUE celdas ocupa cada vibora, no en que orden. Sin el
    orden no se puede saber cual es la cola ni cuando se libera cada casilla,
    y el bot termina siendo o suicida o paranoico.

    Truco: entre dos turnos mios cada vibora avanzo exactamente 1 paso. Asi que
    el orden nuevo es [cabeza_nueva] + (orden viejo filtrado por las celdas que
    siguen ocupadas). El conjunto observado es la verdad absoluta sobre QUIEN
    esta, y el historial aporta el orden. Se autocorrige solo.
    """

    def __init__(self):
        self.order = []

    def update(self, head, cells):
        if head is None:
            self.order = []
            return self.order
        prev = self.order
        new = [head]
        seen = {head}
        for cell in prev:
            if cell in cells and cell not in seen:
                new.append(cell)
                seen.add(cell)
        if len(new) != len(cells):
            # Arranque en frio o desincronizacion -> reconstruyo caminando.
            new = self._walk(head, cells)
        self.order = new
        return new

    @staticmethod
    def _walk(head, cells):
        """Reconstruccion por caminata (solo para el primer turno: la vibora
        arranca recta de largo 3, asi que no hay ambiguedad)."""
        order = [head]
        seen = {head}
        cur = head
        while len(order) < len(cells):
            nxt = None
            for _, (dr, dc) in DIR_ITEMS:
                nb = (cur[0] + dr, cur[1] + dc)
                if nb in cells and nb not in seen:
                    nxt = nb
                    break
            if nxt is None:
                break
            order.append(nxt)
            seen.add(nxt)
            cur = nxt
        for cell in cells:
            if cell not in seen:
                order.append(cell)
        return order


# ============================================================================
#  CEREBRO
# ============================================================================

class Brain:
    """Decide el movimiento. Un Brain por proceso; estado por game_id."""

    def __init__(self):
        self.games = {}
        self.timings = []
        self.eaten_ok = self.eaten_x = self.eaten_bad = 0

    def _game(self, game_id):
        g = self.games.get(game_id)
        if g is None:
            g = {'me': BodyTracker(), 'opp': BodyTracker()}
            self.games[game_id] = g
        return g

    # ---------------- utilidades de grilla ----------------

    _grid_cache = {}

    def _setup_grid(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self._rate = (DIGIT_RATE_K / (rows + cols)) if DIGIT_RATE_ADAPTIVE else DIGIT_RATE
        cached = Brain._grid_cache.get((rows, cols))
        if cached is not None:
            self.nbrs, self.exp_dist = cached
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

        # exp_dist[celda] = distancia Manhattan ESPERADA hasta una casilla al azar.
        # Los objetivos reaparecen en posiciones aleatorias, asi que terminar un
        # plan en el centro abarata todos los objetivos que vengan despues. Este
        # es el termino que convierte un bot glotón en uno que gestiona posicion.
        def axis(n):
            return [(k * (k + 1) / 2.0 + (n - 1 - k) * (n - k) / 2.0) / n
                    for k in range(n)]
        er, ec = axis(rows), axis(cols)
        base = min(er) + min(ec)
        exp_dist = {(r, c): er[r] + ec[c] - base
                    for r in range(rows) for c in range(cols)}
        Brain._grid_cache[(rows, cols)] = (nbrs, exp_dist)
        self.nbrs, self.exp_dist = nbrs, exp_dist

    @staticmethod
    def _occupancy(my_order, opp_order):
        """celda -> instante a partir del cual queda libre.

        El segmento i de una vibora de largo L se libera dentro de L-i pasos
        (la cola, i=L-1, se libera en 1). Entrar a la celda en t es legal si
        t >= ese valor.
        """
        occ = {}
        L = len(my_order)
        for i, cell in enumerate(my_order):
            occ[cell] = L - i
        Lo = len(opp_order)
        for j, cell in enumerate(opp_order):
            v = Lo - j
            if v > occ.get(cell, 0):
                occ[cell] = v
        return occ

    def _bfs(self, start, occ, blocked, with_parents=False):
        """BFS respetando el tiempo de liberacion de cada celda.

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
    def _path_from(parent, cell):
        path = []
        cur = cell
        while parent.get(cur) is not None:
            path.append(cur)
            cur = parent[cur]
        path.reverse()
        return path

    # ---------------- simulacion y seguridad ----------------

    @staticmethod
    def _advance(order, path, growth_cells):
        """Mueve mi vibora por el camino. Crece en las celdas de growth_cells."""
        cur = list(order)
        for cell in path:
            if cell in growth_cells:
                cur = [cell] + cur
            else:
                cur = [cell] + cur[:-1]
        return cur

    def _is_safe(self, my_order, opp_order, opp_head=None):
        """Despues de la jugada, ¿llego a mi propia cola y me queda aire?

        Alcanzar la cola significa que puedo seguir girando indefinidamente:
        es el criterio clasico de no-encierro y el unico realmente confiable.

        Dos correcciones que importan mucho en la practica:
          - Mi cuerpo se libera UN paso mas tarde de lo optimista, porque cada
            digito que como retrasa la cola. Sin este margen el bot se enrosca
            confiando en un pasillo que en realidad todavia esta ocupado.
          - Las casillas a las que el rival puede mover en su proximo turno se
            consideran ocupadas: asi veo el aplastamiento contra la pared ANTES
            de meterme.
        """
        head = my_order[0]
        L = len(my_order)
        occ = {}
        for i, cell in enumerate(my_order):
            occ[cell] = L - i + 1          # +1: margen por crecimiento
        Lo = len(opp_order)
        for j, cell in enumerate(opp_order):
            v = Lo - j
            if v > occ.get(cell, 0):
                occ[cell] = v
        if opp_head is not None:
            for nb in self.nbrs.get(opp_head, ()):
                if occ.get(nb, 0) < 2:
                    occ[nb] = 2
        reach = self._bfs(head, occ, frozenset())
        area = len(reach)
        if area < L + SAFETY_MARGIN:
            return False, area
        return (my_order[-1] in reach), area

    # ---------------- valoracion ----------------

    def future_base(self, moves_left):
        """Puntos base de digitos que espero cosechar en lo que queda.

        Es exactamente lo que agrega subir el multiplicador en +1, porque el
        multiplicador es aditivo: cada digito futuro suma una base mas.

        La tasa se adapta al tablero: en uno de 20x20 cada bocado cuesta casi
        el doble de movimientos que en uno de 12x12, asi que voy a comer la
        mitad de digitos y una X vale la mitad. Con la tasa fija sobrevaloraba
        las X en tableros grandes y las subvaloraba en los chicos.
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

    # ---------------- decision principal ----------------

    def decide(self, td):
        t0 = time.perf_counter()
        deadline = t0 + MOVE_BUDGET
        hard = t0 + HARD_BUDGET

        side = (td.get('side') or 'A').upper()
        opp_side = 'B' if side == 'A' else 'A'
        rows = td.get('rows')
        cols = td.get('cols')
        if not rows or not cols:
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

        ent = scan(grid)
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
        my_order = g['me'].update(head, my_cells)
        opp_order = g['opp'].update(opp_head, opp_cells) if opp_head else []

        if side == 'A':
            my_mult = td.get('multiplier_1', 1) or 1
            opp_mult = td.get('multiplier_2', 1) or 1
            my_score = td.get('score_1', 0)
            opp_score = td.get('score_2', 0)
        else:
            my_mult = td.get('multiplier_2', 1) or 1
            opp_mult = td.get('multiplier_1', 1) or 1
            my_score = td.get('score_2', 0)
            opp_score = td.get('score_1', 0)

        rem = td.get('remaining_moves', 0) or 0
        my_moves = (rem + 1) // 2          # movimientos que me quedan a mi

        digits = ent['digits']
        tgt_cell, tgt_val = target_digit(digits)

        # Los 5 digitos en pantalla son consecutivos ciclicos, asi que puedo
        # ordenarlos y saber de antemano la secuencia completa de objetivos.
        seq = []
        if tgt_val is not None:
            by_val = {v: cell for cell, v in digits.items()}
            v = tgt_val
            for _ in range(len(digits)):
                if v not in by_val:
                    break
                seq.append((by_val[v], v))
                v = 1 if v == 9 else v + 1
        nxt_cell, nxt_val = (seq[1] if len(seq) > 1 else (None, None))

        # Digitos que NO hay que comer todavia: -500 cada uno. Obstaculos.
        wrong = {cell for cell in digits if cell != tgt_cell}

        snake_cells = my_cells | opp_cells

        # ---- movimientos legales (exacto: el juego es por turnos) ----
        legal = []
        for name, (dr, dc) in DIR_ITEMS:
            nb = (head[0] + dr, head[1] + dc)
            if 0 <= nb[0] < rows and 0 <= nb[1] < cols and nb not in snake_cells:
                legal.append((name, nb))
        if not legal:
            return self._desperate(head, rows, cols, snake_cells)

        # ---- evaluacion base de cada movimiento legal (fallback siempre listo) ----
        base = {}
        for name, cell in legal:
            order1 = self._advance(my_order, [cell],
                                   {tgt_cell} | wrong if tgt_cell else wrong)
            safe, area = self._is_safe(order1, opp_order, opp_head)
            base[name] = {'cell': cell, 'order': order1, 'safe': safe,
                          'area': area, 'tail_ok': safe}

        # ---- distancias del rival, para las carreras ----
        opp_dist = {}
        if opp_head is not None:
            opp_dist = self._bfs(opp_head, self._occupancy(opp_order, my_order),
                                 frozenset(wrong))

        # ---- busqueda en haz sobre cadenas de objetivos ----
        plans = self._beam(my_order, opp_order, seq, ent['X'], my_moves,
                           my_mult, opp_mult, opp_dist, side, deadline)

        # ---- validar seguridad y elegir ----
        best = None
        for p in sorted(plans, key=lambda p: p['score'], reverse=True):
            if time.perf_counter() > hard and best is not None:
                break
            # Guarda dura: el plan puede APUNTAR a un digito que todavia no esta
            # activo (esperando que el rival avance la secuencia), pero jamas
            # puede PISARLO antes de tiempo: eso son -500 seguros.
            if p['first'] in wrong:
                continue
            b = base.get(self._dir_name(head, p['first']))
            if b is None or not b['safe']:
                continue
            # Valido sobre el primer tramo, que es exacto: los tramos
            # profundos usan una forma aproximada del cuerpo.
            if not self._is_safe(p['order1'], opp_order, opp_head)[0]:
                continue
            best = p
            break

        if best is not None:
            direction = self._dir_name(head, best['first'])
        else:
            direction = self._survival(base, my_order, opp_order, opp_head,
                                       wrong, tgt_cell, nxt_cell, rows, cols,
                                       my_score, opp_score, my_moves)

        # Contabilidad de lo comido: miro que hay en la casilla a la que voy.
        dr, dc = DIRS[direction]
        dest = (head[0] + dr, head[1] + dc)
        if dest in digits:
            if dest == tgt_cell:
                self.eaten_ok += 1
            else:
                self.eaten_bad += 1
        elif dest in ent['X']:
            self.eaten_x += 1

        self.last = {'plans': len(plans), 'best': best, 'dir': direction,
                     'survival': best is None, 'tgt': tgt_cell, 'tval': tgt_val,
                     'seq': seq, 'wrong': wrong, 'head': head,
                     'my_moves': my_moves, 'legal': [n for n, _ in legal],
                     'base': {k: (v['safe'], v['area']) for k, v in base.items()}}
        self.timings.append(time.perf_counter() - t0)
        return direction

    # ---------------- piezas de la decision ----------------

    @staticmethod
    def _loses_race(k, ko, side):
        """El juego es por turnos: A mueve antes que B en cada ronda."""
        return k > ko if side == 'A' else k >= ko

    def _beam(self, my_order, opp_order, seq, xs, my_moves, my_mult, opp_mult,
              opp_dist, side, deadline):
        """Busqueda en haz sobre CADENAS de objetivos.

        Un plan no es "ir a la manzana mas cercana" sino una secuencia: comer
        el 6, despues el 7 que ya esta a la vista, despues una X. Como los 5
        digitos del tablero son consecutivos, la secuencia completa es conocida
        de antemano y se puede planificar varios bocados por adelantado.

        El puntaje de un plan es (puntos + bocado_tipico) / (movimientos +
        costo_de_reposicionamiento). El segundo termino es clave: como el
        proximo objetivo aparecera en una casilla al azar, terminar el plan
        cerca del centro vale movimientos reales mas adelante.
        """
        head = my_order[0]
        start = {
            'cell': head, 'order': my_order, 'moves': 0, 'value': 0.0,
            'first': None, 'order1': None, 'si': 0,
            'xs': frozenset(xs), 'score': -1e18,
        }
        beam = [start]
        done = []
        typical = self._typical_value(seq, my_moves, my_mult, opp_mult)

        for leg in range(MAX_LEGS):
            nxt = []
            for st in beam:
                if time.perf_counter() > deadline and leg > 0:
                    break
                budget = my_moves - st['moves']
                if budget <= 0:
                    continue
                # objetivos vivos desde este estado
                active = seq[st['si']] if st['si'] < len(seq) else None
                hazards = {c for c, _ in seq[st['si'] + 1:]}
                occ = self._occupancy(st['order'], opp_order)
                want_parent = (st['first'] is None)
                res = self._bfs(st['cell'], occ, frozenset(hazards),
                                with_parents=want_parent)
                dist, parent = res if want_parent else (res, None)

                targets = []
                if active is not None and active[0] in dist:
                    targets.append((active[0], 'digit', active[1], dist[active[0]]))
                for x in st['xs']:
                    if x in dist:
                        targets.append((x, 'X', None, dist[x]))

                # ---- DEJARSELO AL RIVAL Y TOMAR EL DE ARRIBA ----
                # Los digitos bajos son mal negocio: un 1 a multiplicador 5 son
                # 500 puntos por varios movimientos, cuando el promedio de la
                # partida ronda los 160 por movimiento. Si encima el rival llega
                # antes, conviene que EL queme el barato (la secuencia avanza
                # igual para los dos) y quedarme yo con el de arriba, que vale
                # hasta nueve veces mas. Como los cinco digitos en pantalla son
                # consecutivos, ya se exactamente donde esta.
                skip_parent = None
                if (SKIP_ENABLED and want_parent and active is not None
                        and st['si'] + 1 < len(seq)):
                    ko_act = opp_dist.get(active[0])
                    k_act = dist.get(active[0])
                    if ko_act is not None and (
                            k_act is None or self._loses_race(k_act, ko_act, side)):
                        ncell, nval = seq[st['si'] + 1]
                        d2, skip_parent = self._bfs(
                            st['cell'], occ, frozenset(hazards - {ncell}),
                            with_parents=True)
                        if ncell in d2:
                            # No es comestible hasta que el rival coma el activo.
                            targets.append((ncell, 'skip', nval,
                                            max(d2[ncell], ko_act + 1)))

                for cell, kind, dval, k in targets:
                    if k <= 0 or k > budget or k > MAX_PLAN_LEN:
                        continue

                    # ---- CARRERA CONTRA EL RIVAL (la tecnica del bot viejo) ----
                    # Comparo tiempos TOTALES desde ahora, no solo del primer
                    # tramo: si el rival pisa el objetivo antes que yo, ir para
                    # alla es tirar movimientos. El juego es por turnos, asi que
                    # el empate lo gana el que mueve primero en la ronda.
                    lost = False
                    if kind != 'skip':
                        ko = opp_dist.get(cell)
                        lost = (ko is not None and
                                self._loses_race(st['moves'] + k, ko, side))

                    if kind == 'digit':
                        v = self._digit_value(dval, my_mult, opp_mult)
                        if lost:
                            if RACE_HARD_FILTER:
                                continue
                            v *= LOST_RACE_DIGIT
                    elif kind == 'skip':
                        v = self._digit_value(dval, my_mult, opp_mult) * SKIP_W
                    else:
                        v = self._x_value(my_moves - st['moves'] - k)
                        if lost:
                            if RACE_HARD_FILTER:
                                continue
                            v *= LOST_RACE_X
                    v *= LEG_CONFIDENCE ** leg
                    if v <= 0:
                        continue

                    if want_parent:
                        path = self._path_from(
                            skip_parent if kind == 'skip' else parent, cell)
                        if not path:
                            continue
                        first = path[0]
                        order = self._advance(
                            st['order'], path,
                            {cell} if kind in ('digit', 'skip') else frozenset())
                    else:
                        first = st['first']
                        # Tramos profundos: no reconstruyo el camino exacto,
                        # solo actualizo largo y posicion (basta para valorar).
                        order = self._shift(st['order'], cell, k,
                                            kind in ('digit', 'skip'))

                    ns = {
                        'cell': cell, 'order': order,
                        'moves': st['moves'] + k, 'value': st['value'] + v,
                        'first': first,
                        'order1': order if want_parent else st['order1'],
                        'si': st['si'] + (2 if kind == 'skip'
                                          else 1 if kind == 'digit' else 0),
                        'xs': st['xs'] - {cell} if kind == 'X' else st['xs'],
                    }
                    ns['score'] = ((ns['value'] + typical) /
                                   (ns['moves'] + REPOS_W * self.exp_dist[cell]))
                    nxt.append(ns)

            if not nxt:
                break
            nxt.sort(key=lambda s: s['score'], reverse=True)
            done.extend(nxt[:BEAM_WIDTH])
            beam = nxt[:BEAM_WIDTH]
            if time.perf_counter() > deadline:
                break
        return done

    def _typical_value(self, seq, my_moves, my_mult, opp_mult):
        """Valor del bocado 'promedio' disponible ahora. Sirve de referencia
        para que el costo de reposicionamiento tenga escala."""
        vals = [self._x_value(my_moves)]
        if seq:
            vals.append(self._digit_value(seq[0][1], my_mult, opp_mult))
        return sum(vals) / len(vals)

    @staticmethod
    def _shift(order, cell, k, grew):
        """Aproxima el cuerpo tras avanzar k pasos hasta 'cell' (tramos profundos).
        No necesito la forma exacta, solo el largo y donde quedo la cabeza."""
        keep = max(len(order) - k, 0)
        new = [cell] + order[:keep]
        if grew:
            new.append(order[keep] if keep < len(order) else cell)
        return new

    def _survival(self, base, my_order, opp_order, opp_head, wrong,
                  tgt_cell, nxt_cell, rows, cols, my_score, opp_score, my_moves):
        """Ningun plan valido: elijo el movimiento que mas futuro me deja.

        Prioriza espacio, castiga pisar digitos equivocados y mantiene un
        empujon suave hacia el objetivo para no perder posicion.
        """
        losing_badly = (opp_score - my_score) > 3000 and my_moves < 30
        pull = tgt_cell or nxt_cell

        best_name, best_score = None, None
        for name, b in base.items():
            cell = b['cell']
            s = b['area'] * 10.0
            if b['safe']:
                s += 5000.0
            else:
                # Sin cola alcanzable al menos elijo la bolsa de aire mas grande.
                ok2, area2 = self._is_safe(b['order'], opp_order)
                if ok2:
                    s += 2000.0
            if cell in wrong:
                s -= WRONG_DIGIT_COST * (0.3 if losing_badly else 1.0)
            # Alejarme del borde: pegado a la pared pierdo la mitad de las salidas.
            r, c = cell
            if r == 0 or r == rows - 1:
                s -= 15.0
            if c == 0 or c == cols - 1:
                s -= 15.0
            if pull is not None:
                s -= 2.0 * (abs(cell[0] - pull[0]) + abs(cell[1] - pull[1]))
            if opp_head is not None:
                # Quedar cerca del rival es bueno: el que se mete en el otro pierde,
                # y aca el que se mete es siempre el que mueve.
                d = abs(cell[0] - opp_head[0]) + abs(cell[1] - opp_head[1])
                if d <= 1:
                    s -= 30.0
            if best_score is None or s > best_score:
                best_name, best_score = name, s
        return best_name or next(iter(base))

    @staticmethod
    def _dir_name(head, cell):
        dr = cell[0] - head[0]
        dc = cell[1] - head[1]
        for name, d in DIR_ITEMS:
            if d == (dr, dc):
                return name
        return 'up'

    @staticmethod
    def _desperate(head, rows, cols, snake_cells):
        """Sin salidas legales: al menos no me tiro contra la pared."""
        for name, (dr, dc) in DIR_ITEMS:
            nb = (head[0] + dr, head[1] + dc)
            if 0 <= nb[0] < rows and 0 <= nb[1] < cols:
                return name
        return 'up'


BRAIN = Brain()


def choose_direction(turn_data):
    """Punto de entrada del cerebro, con red de seguridad total."""
    try:
        return BRAIN.decide(turn_data)
    except Exception as e:                      # nunca crashear: un crash = timeout
        print('brain error: {}'.format(e))
        try:
            return _fallback(turn_data)
        except Exception:
            return 'up'


def _fallback(td):
    """Movimiento legal cualquiera, sin inteligencia, si el cerebro falla."""
    side = (td.get('side') or 'A').upper()
    grid = parse_board(td.get('board', ''), td.get('rows'), td.get('cols'))
    ent = scan(grid)
    head = ent[side]
    if head is None:
        return 'up'
    occupied = ent['a'] | ent['b']
    if ent['A']:
        occupied.add(ent['A'])
    if ent['B']:
        occupied.add(ent['B'])
    rows, cols = len(grid), len(grid[0])
    digits = ent['digits']
    tgt, tv = target_digit(digits)
    wrong = {c for c in digits if c != tgt}
    best = None
    for name, (dr, dc) in DIRS.items():
        nb = (head[0] + dr, head[1] + dc)
        if 0 <= nb[0] < rows and 0 <= nb[1] < cols and nb not in occupied:
            score = 0 if nb in wrong else 1
            if best is None or score > best[0]:
                best = (score, name)
    return best[1] if best else 'up'


# ============================================================================
#  CONEXION AL SERVIDOR
# ============================================================================

async def send(websocket, action, data):
    message = json.dumps({'action': action, 'data': data})
    await websocket.send(message)


async def process_your_turn(websocket, request_data):
    data = request_data['data']
    direction = choose_direction(data)
    move = {
        'game_id': data['game_id'],
        'turn_token': data['turn_token'],
        'direction': direction,
    }
    log_action(move['game_id'], {'action': 'move', 'data': move})
    await send(websocket, 'move', move)
    if BRAIN.timings:
        print('mv {} | dir {:<5} | {:.1f} ms'.format(
            data.get('remaining_moves'), direction, BRAIN.timings[-1] * 1000))


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
                log_event(request_data['data']['game_id'], request_data)
                write_live({**request_data['data'], 'event': 'your_turn'})
                await process_your_turn(websocket, request_data)

            elif event == 'game_over':
                write_live({**request_data['data'], 'event': 'game_over'})
                game_id = request_data['data'].get('game_id')
                if game_id:
                    log_event(game_id, request_data)
                    write_game_log(game_id)
                    BRAIN.games.pop(game_id, None)
                d = request_data['data']
                side = 'A' if d.get('player_1') == d.get('winner') else None
                print('-' * 62)
                print('FIN | bot {} | score_1 {} (x{}) | score_2 {} (x{}) | gana {}'
                      .format(BOT_VERSION, d.get('score_1'), d.get('multiplier_1'),
                              d.get('score_2'), d.get('multiplier_2'), d.get('winner')))
                print('comidos: {} digitos correctos, {} X, {} digitos ERRADOS'
                      .format(BRAIN.eaten_ok, BRAIN.eaten_x, BRAIN.eaten_bad))
                if BRAIN.eaten_bad:
                    print('AVISO: comer un digito equivocado son -500. Si este numero '
                          'no es 0, mandame el log.')
                if BRAIN.timings:
                    ts = BRAIN.timings
                    print('tiempo por jugada: medio {:.1f} ms | maximo {:.1f} ms'
                          .format(1000 * sum(ts) / len(ts), 1000 * max(ts)))
                print('-' * 62)
                BRAIN.timings = []
                BRAIN.eaten_ok = BRAIN.eaten_x = BRAIN.eaten_bad = 0

            elif event == 'error':
                print('server error: {}'.format(request_data.get('data')))

        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            print('error {}'.format(e))
            break


async def start(auth_token):
    uri = "wss://server.codechallenge.net.ar/ws?token={}".format(auth_token)
    while True:
        try:
            print('=' * 62)
            print(' BOT SNAKE {} | reglas v4: digitos + X + tablero variable'
                  .format(BOT_VERSION))
            print(' presupuesto por jugada: {:.0f} ms'.format(MOVE_BUDGET * 1000))
            print('=' * 62)
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


if __name__ == '__main__':
    if len(sys.argv) >= 2:
        asyncio.run(start(sys.argv[1]))
    else:
        print('please provide your auth_token')
