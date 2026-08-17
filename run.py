import asyncio
import json
import os
import sys
import time
from collections import deque

import websockets


# ============================================================================
#  Bot de Snake para codechallenge
#  Uso:  python run.py <TU_TOKEN>
#  Toda la "inteligencia" está en choose_direction(). El resto es la conexión
#  al servidor (websocket), aceptar desafíos y loguear, igual que el cliente
#  de referencia.
# ============================================================================


# Un log de texto por partida (eventos recibidos / acciones enviadas),
# se escribe a game_<game_id>.log cuando termina el match.
HISTORY = {}

# Direcciones: nombre -> (delta_fila, delta_columna). Fila 0 = arriba.
DIRS = {
    'up': (-1, 0),
    'down': (1, 0),
    'left': (0, -1),
    'right': (0, 1),
}


def log_event(game_id, message):
    HISTORY.setdefault(game_id, []).append('< ' + json.dumps(message))


def log_action(game_id, message):
    HISTORY.setdefault(game_id, []).append('> ' + json.dumps(message))


def write_game_log(game_id):
    try:
        with open(f"game_{game_id}.log", "w") as f:
            f.write("\n".join(HISTORY.get(game_id, [])) + "\n")
        print(f"saved game_{game_id}.log")
    except OSError as e:
        print(f"could not write game log: {e}")


def write_live(data):
    """Escribe el estado actual a live.json, que lee el visor 'en vivo'.
    Escritura atómica (temporal + replace) para que el visor nunca lea un
    archivo a medio escribir."""
    try:
        with open("live.tmp", "w") as f:
            json.dump(data, f)
        os.replace("live.tmp", "live.json")
    except OSError:
        pass


# ============================================================================
#  CEREBRO DEL BOT
# ============================================================================

def parse_board(board):
    """Convierte el string del tablero en una grilla 2D (lista de listas).
    Cada fila viene como |...| y las filas están unidas por saltos de línea."""
    raw = [ln for ln in board.split('\n') if ln != '']
    rows = []
    for ln in raw:
        if ln.startswith('|'):
            ln = ln[1:]
        if ln.endswith('|'):
            ln = ln[:-1]
        rows.append(ln)
    width = max((len(r) for r in rows), default=0)
    # Relleno con espacios para que la grilla sea rectangular.
    return [list(r.ljust(width)) for r in rows]


def heuristic_move(grid, side):
    """Elige movimiento con criterio competitivo:
      - Seguridad dura: nunca a pared / cuerpo / rival (evita el -500).
      - Flood-fill: no entrar en un espacio donde no entra mi propio largo.
      - Voronoi: preferir movimientos que me den más territorio que al rival
        (esto evita que me arrincone contra la pared, que es como perdí antes).
      - Penalización de bordes: no pegarme a las paredes salvo que convenga.
      - Comida: entre los movimientos cómodos (con espacio de sobra), ir a la
        más cercana. La supervivencia manda; la comida es secundaria.
      - Las colas se tratan como libres al medir el espacio, porque se mueven.
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    my_head = side.upper()
    my_body = side.lower()
    opp_head = 'B' if my_head == 'A' else 'A'
    opp_body = opp_head.lower()

    head = None
    opp_head_pos = None
    my_cells = set()
    opp_cells = set()
    food = set()

    for r in range(rows):
        for c in range(len(grid[r])):
            ch = grid[r][c]
            if ch == my_head:
                head = (r, c); my_cells.add((r, c))
            elif ch == my_body:
                my_cells.add((r, c))
            elif ch == opp_head:
                opp_head_pos = (r, c); opp_cells.add((r, c))
            elif ch == opp_body:
                opp_cells.add((r, c))
            elif ch == '*':
                food.add((r, c))

    if head is None:
        return 'up'

    all_snake = my_cells | opp_cells
    my_length = len(my_cells)
    cr, cc = (rows - 1) / 2.0, (cols - 1) / 2.0

    def in_bounds(r, c):
        return 0 <= r < rows and 0 <= c < cols

    def reconstruct_tail(start, cells):
        # Camina desde la cabeza por celdas adyacentes del mismo cuerpo;
        # el último es (aprox.) la cola. Solo se usa para estimar espacio.
        if start is None:
            return None
        visited = {start}; cur = start
        while True:
            nxt = None
            for dr, dc in DIRS.values():
                nb = (cur[0] + dr, cur[1] + dc)
                if nb in cells and nb not in visited:
                    nxt = nb; break
            if nxt is None:
                break
            visited.add(nxt); cur = nxt
        return cur

    my_tail = reconstruct_tail(head, my_cells)
    opp_tail = reconstruct_tail(opp_head_pos, opp_cells)

    # Próximas casillas posibles del rival: se van a "ocupar", así que al medir
    # el espacio las tratamos como bloqueadas. Esto hace que un pasillo que el
    # rival está por cerrar se vea como callejón ANTES de que me meta.
    opp_threat = set()
    if opp_head_pos is not None:
        for dr, dc in DIRS.values():
            nb = (opp_head_pos[0] + dr, opp_head_pos[1] + dc)
            if in_bounds(*nb) and nb not in all_snake:
                opp_threat.add(nb)

    def eval_obstacles(candidate):
        # Obstáculos para MEDIR espacio (no para decidir seguridad).
        obs = set(all_snake)
        if opp_tail is not None:
            obs.discard(opp_tail)          # la cola rival se moverá
        if my_tail is not None and candidate not in food:
            obs.discard(my_tail)           # mi cola se mueve salvo que coma
        obs |= opp_threat                  # amenaza del rival (cierre de pasillos)
        obs.discard(candidate)             # nunca bloqueo mi propia casilla destino
        return obs

    def flood_area(start, obs):
        seen = {start}; q = deque([start]); n = 0
        while q:
            r, c = q.popleft(); n += 1
            for dr, dc in DIRS.values():
                nb = (r + dr, c + dc)
                if in_bounds(*nb) and nb not in seen and nb not in obs:
                    seen.add(nb); q.append(nb)
        return n

    def dist_map(start, obs):
        seen = {start: 0}; q = deque([start])
        while q:
            r, c = q.popleft(); d = seen[(r, c)]
            for dr, dc in DIRS.values():
                nb = (r + dr, c + dc)
                if in_bounds(*nb) and nb not in seen and nb not in obs:
                    seen[nb] = d + 1; q.append(nb)
        return seen

    def dist_to_food(start, obs):
        if not food:
            return None
        if start in food:
            return 0
        seen = {start}; q = deque([(start, 0)])
        while q:
            (r, c), d = q.popleft()
            for dr, dc in DIRS.values():
                nb = (r + dr, c + dc)
                if not in_bounds(*nb) or nb in seen or nb in obs:
                    continue
                if nb in food:
                    return d + 1
                seen.add(nb); q.append((nb, d + 1))
        return None

    def voronoi(start, obs):
        mine = dist_map(start, obs)
        if opp_head_pos is None:
            return len(mine)
        theirs = dist_map(opp_head_pos, obs)
        ctrl = 0
        for cell, dm in mine.items():
            do = theirs.get(cell)
            if do is None or dm < do:   # llego yo primero (o solo yo)
                ctrl += 1
        return ctrl

    def edge_penalty(cell):
        r, c = cell
        p = 0
        if r == 0 or r == rows - 1:
            p += 4
        if c == 0 or c == cols - 1:
            p += 4
        return p

    # --- movimientos seguros (seguridad dura, sin liberar colas) ---
    candidates = []
    for name, (dr, dc) in DIRS.items():
        nb = (head[0] + dr, head[1] + dc)
        if in_bounds(*nb) and nb not in all_snake:
            candidates.append((name, nb))

    if not candidates:
        for name, (dr, dc) in DIRS.items():
            nb = (head[0] + dr, head[1] + dc)
            if in_bounds(*nb):
                return name
        return 'up'

    scored = []
    for name, cell in candidates:
        obs = eval_obstacles(cell)
        area = flood_area(cell, obs)
        vor = voronoi(cell, obs)
        fd = dist_to_food(cell, obs)
        edge = edge_penalty(cell)
        cent = -(abs(cell[0] - cr) + abs(cell[1] - cc))
        scored.append({'name': name, 'cell': cell, 'area': area,
                       'vor': vor, 'fd': fd, 'edge': edge, 'cent': cent})

    # Puerta de supervivencia: que quepa mi largo en el área.
    survivable = [s for s in scored if s['area'] >= my_length]
    pool = survivable if survivable else scored

    def space(s):
        return s['area'] + s['vor'] - s['edge']

    best = max(space(s) for s in pool)
    # "Cómodos": no sacrifican espacio significativo (>=80% del mejor).
    comfy = [s for s in pool if space(s) >= 0.8 * best] if best > 0 else pool

    food_opts = [s for s in comfy if s['fd'] is not None]
    if food_opts:
        pick = min(food_opts, key=lambda s: (s['fd'], -space(s), -s['cent']))
    else:
        pick = max(comfy, key=lambda s: (space(s), s['cent']))
    return pick['name']


# ============================ MINIMAX (lookahead) ============================
import time as _time

_MM_BUDGET = 0.10       # presupuesto por jugada (s); con margen bajo el limite
_MM_MAX_DEPTH = 6       # profundidad max en plies (mis jugadas + del rival)
_WIN = 1_000_000        # rival encerrado: bueno, pero NO infinito
_LOSE = -1_000_000_000  # yo encerrado: evitar a toda costa (paranoico)


class _Timeout(Exception):
    pass


def _build_state(grid, side):
    rows = len(grid); cols = len(grid[0]) if rows else 0
    mh = side.upper(); mb = side.lower()
    oh = 'B' if mh == 'A' else 'A'; ob = oh.lower()
    me_head = op_head = None
    me_body = set(); op_body = set(); food = set()
    for r in range(rows):
        row = grid[r]
        for c in range(len(row)):
            ch = row[c]
            if ch == mh: me_head = (r, c); me_body.add((r, c))
            elif ch == mb: me_body.add((r, c))
            elif ch == oh: op_head = (r, c); op_body.add((r, c))
            elif ch == ob: op_body.add((r, c))
            elif ch == '*': food.add((r, c))
    return {'rows': rows, 'cols': cols, 'mh': me_head, 'mb': me_body,
            'oh': op_head, 'ob': op_body, 'food': food, 'eaten': 0}


def _legal(state, mine):
    head = state['mh'] if mine else state['oh']
    if head is None:
        return []
    own = state['mb'] if mine else state['ob']
    other = state['ob'] if mine else state['mb']
    rows, cols = state['rows'], state['cols']
    out = []
    for name, (dr, dc) in DIRS.items():
        nh = (head[0] + dr, head[1] + dc)
        if 0 <= nh[0] < rows and 0 <= nh[1] < cols and nh not in own and nh not in other:
            out.append((name, nh, nh in state['food']))
    return out


def _apply(state, mine, nh, ate):
    # Pesimista: NO remuevo la cola (no la identifico con certeza). Sobreestima
    # el bloqueo -> lado seguro, y ayuda a "ver" cierres de pasillo del rival.
    ns = dict(state)
    if mine:
        nb = set(state['mb']); nb.add(nh); ns['mb'] = nb; ns['mh'] = nh
    else:
        nb = set(state['ob']); nb.add(nh); ns['ob'] = nb; ns['oh'] = nh
    if ate:
        f = set(state['food']); f.discard(nh); ns['food'] = f
        if mine:
            ns['eaten'] = state['eaten'] + 1
    return ns


def _flood(start, blocked, rows, cols):
    seen = {start}; q = deque([start]); n = 0
    while q:
        r, c = q.popleft(); n += 1
        for dr, dc in DIRS.values():
            nb = (r + dr, c + dc)
            if 0 <= nb[0] < rows and 0 <= nb[1] < cols and nb not in seen and nb not in blocked:
                seen.add(nb); q.append(nb)
    return n


def _distmap(start, blocked, rows, cols):
    seen = {start: 0}; q = deque([start])
    while q:
        r, c = q.popleft(); d = seen[(r, c)]
        for dr, dc in DIRS.values():
            nb = (r + dr, c + dc)
            if 0 <= nb[0] < rows and 0 <= nb[1] < cols and nb not in seen and nb not in blocked:
                seen[nb] = d + 1; q.append(nb)
    return seen


def _evaluate(state):
    mh = state['mh']
    if mh is None:
        return float(_LOSE)
    rows, cols = state['rows'], state['cols']
    blocked = state['mb'] | state['ob']
    area = _flood(mh, blocked, rows, cols)
    mine = _distmap(mh, blocked, rows, cols)
    oh = state['oh']
    theirs = _distmap(oh, blocked, rows, cols) if oh is not None else None
    if theirs is None:
        control = len(mine)
    else:
        control = 0
        for cell, dm in mine.items():
            do = theirs.get(cell)
            if do is None or dm < do:
                control += 1
    # Imán de comida: me guío SOLO por la manzana que gano yo la carrera
    # (empate = mía, muevo primero). No pierdo tiempo con las que gana el rival.
    food_bonus = 0.0
    if state['food']:
        best = None
        for f in state['food']:
            dm = mine.get(f)
            if dm is None:
                continue
            if theirs is not None:
                do = theirs.get(f)
                if do is not None and do < dm:
                    continue
            if best is None or dm < best:
                best = dm
        if best is not None:
            food_bonus = 300.0 / (1 + best)
    r, c = mh
    edge = 0
    if r == 0 or r == rows - 1: edge += 4
    if c == 0 or c == cols - 1: edge += 4
    return area * 2.0 + control * 1.0 + food_bonus + 250.0 * state['eaten'] - edge


def _minimax(state, depth, maximizing, alpha, beta, deadline):
    if _time.perf_counter() > deadline:
        raise _Timeout()
    if depth == 0:
        return _evaluate(state)
    moves = _legal(state, maximizing)
    if not moves:
        return float(_LOSE) if maximizing else float(_WIN)
    if maximizing:
        value = float('-inf')
        for _, nh, ate in moves:
            v = _minimax(_apply(state, True, nh, ate), depth - 1, False, alpha, beta, deadline)
            if v > value: value = v
            if value > alpha: alpha = value
            if alpha >= beta: break
        return value
    else:
        value = float('inf')
        for _, nh, ate in moves:
            v = _minimax(_apply(state, False, nh, ate), depth - 1, True, alpha, beta, deadline)
            if v < value: value = v
            if value < beta: beta = value
            if beta <= alpha: break
        return value


def minimax_move(grid, side):
    state = _build_state(grid, side)
    root = _legal(state, True)
    if not root:
        return None
    scored = []
    for name, nh, ate in root:
        scored.append((name, nh, ate, _evaluate(_apply(state, True, nh, ate))))
    scored.sort(key=lambda x: x[3], reverse=True)
    deadline = _time.perf_counter() + _MM_BUDGET
    best_move = scored[0][0]
    for depth in range(2, _MM_MAX_DEPTH + 1, 2):
        try:
            alpha = float('-inf'); best_v = float('-inf'); cur = best_move
            for name, nh, ate, _ in scored:
                v = _minimax(_apply(state, True, nh, ate), depth - 1, False,
                             alpha, float('inf'), deadline)
                if v > best_v: best_v = v; cur = name
                if v > alpha: alpha = v
            best_move = cur
        except _Timeout:
            break
    return best_move


def choose_direction(grid, side):
    try:
        mv = minimax_move(grid, side)
        if mv is not None:
            return mv
    except Exception:
        pass
    return heuristic_move(grid, side)


# ============================================================================
#  CONEXIÓN AL SERVIDOR (no hace falta tocar nada de acá para abajo)
# ============================================================================

async def send(websocket, action, data):
    message = json.dumps({'action': action, 'data': data})
    print(message)
    await websocket.send(message)


async def process_move(websocket, request_data):
    data = request_data['data']
    side = data.get('side') or 'A'
    board = data.get('board', '')
    print(board)

    # Blindaje: si algo falla parseando, mando un movimiento por defecto en
    # vez de crashear (un crash del proceso = timeout = penalización).
    try:
        grid = parse_board(board)
        direction = choose_direction(grid, side)
    except Exception as e:
        print('brain error {}'.format(e))
        direction = 'up'

    move = {
        'game_id': data['game_id'],
        'turn_token': data['turn_token'],
        'direction': direction,
    }
    log_action(move['game_id'], {'action': 'move', 'data': move})
    await send(websocket, 'move', move)


async def process_your_turn(websocket, request_data):
    await process_move(websocket, request_data)


async def play(websocket):
    while True:
        try:
            request = await websocket.recv()
            print(f"< {request}")
            request_data = json.loads(request)
            event = request_data.get('event')

            if event in ('update_user_list', 'list_users'):
                pass

            elif event == 'challenge':
                # Acepta cualquier desafío automáticamente.
                await send(
                    websocket,
                    'accept_challenge',
                    {'challenge_id': request_data['data']['challenge_id']},
                )

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

            elif event == 'error':
                print('server error: {}'.format(request_data.get('data')))

        except KeyboardInterrupt:
            print('Exiting...')
            break
        except Exception as e:
            print('error {}'.format(str(e)))
            break  # fuerza reconexión


async def start(auth_token):
    uri = "wss://codechallenge-server.up.railway.app/ws?token={}".format(auth_token)
    while True:
        try:
            print('connection to {}'.format(uri))
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
        auth_token = sys.argv[1]
        asyncio.run(start(auth_token))
    else:
        print('please provide your auth_token')