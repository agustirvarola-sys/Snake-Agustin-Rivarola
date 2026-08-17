import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, mock_open
import run

# ============================================================================
#  1. PRUEBAS DE ARCHIVOS, LOGS Y DEPURACIÓN
# ============================================================================

def test_logs_and_files():
    run.HISTORY.clear()
    
    # Probar log_event y log_action
    run.log_event("game1", {"event": "start"})
    run.log_action("game1", {"action": "move"})
    assert len(run.HISTORY["game1"]) == 2

    # Probar escritura exitosa de log de juego
    with patch("builtins.open", mock_open()):
        run.write_game_log("game1")

    # Probar manejo de error OSError en log de juego
    with patch("builtins.open", side_effect=OSError("Error de disco")):
        run.write_game_log("game1")

    # Probar escritura de estado en vivo (atomic write)
    with patch("builtins.open", mock_open()), patch("os.replace"):
        run.write_live({"event": "your_turn"})

    # Probar manejo de error en escritura en vivo
    with patch("builtins.open", side_effect=OSError):
        run.write_live({"event": "your_turn"})

# ============================================================================
#  2. PRUEBAS DEL PARSER DEL TABLERO
# ============================================================================

def test_parse_board():
    raw_board = "|A* |\n| B |\n"
    grid = run.parse_board(raw_board)
    assert grid == [
        ['A', '*', ' '],
        [' ', 'B', ' ']
    ]


# ============================================================================
#  3. PRUEBAS DE LÓGICA Y HEURÍSTICA
# ============================================================================

def test_heuristic_move_cases():
    # Caso 1: Tablero normal con comida y rival
    grid = [
        ['A', 'a', '*'],
        [' ', ' ', ' '],
        ['B', 'b', ' ']
    ]
    move = run.heuristic_move(grid, 'A')
    assert move in run.DIRS

    # Caso 2: Sin cabeza en la grilla (retorna 'up')
    empty_grid = [[' ', ' '], [' ', ' ']]
    assert run.heuristic_move(empty_grid, 'A') == 'up'

    # Caso 3: Sin candidatos seguros (rodeado por el propio cuerpo)
    trapped_grid = [
        ['a', 'A', 'a'],
        ['a', 'a', 'a']
    ]
    assert run.heuristic_move(trapped_grid, 'A') in run.DIRS


# ============================================================================
#  4. PRUEBAS DE MINIMAX Y ELECCIÓN DE DIRECCIÓN
# ============================================================================

def test_minimax_and_brain():
    grid = [
        ['A', '*', ' '],
        ['a', ' ', ' '],
        [' ', 'B', 'b']
    ]
    
    # Elección normal vía Minimax
    move = run.choose_direction(grid, 'A')
    assert move in run.DIRS

    # Fallback a heurística cuando Minimax lanza una excepción
    with patch("run.minimax_move", side_effect=Exception("Fallo simulación")):
        assert run.choose_direction(grid, 'A') in run.DIRS

    # Cobertura de Timeout en Minimax
    with patch("run._time.perf_counter", side_effect=[0, 1000]):
        state = run._build_state(grid, 'A')
        with pytest.raises(run._Timeout):
            run._minimax(state, 2, True, -1000, 1000, 0.01)


# ============================================================================
#  5. PRUEBAS ASÍNCRONAS Y COMUNICACIÓN WEBSOCKET
# ============================================================================

@pytest.mark.asyncio
async def test_send():
    ws = AsyncMock()
    await run.send(ws, "test", {"key": "val"})
    ws.send.assert_called_once()


@pytest.mark.asyncio
async def test_process_move_and_exceptions():
    ws = AsyncMock()
    
    # Movimiento exitoso
    req_data = {
        'data': {
            'side': 'A',
            'board': '|A* |\n|   |\n',
            'game_id': 'g123',
            'turn_token': 'tok123'
        }
    }
    await run.process_move(ws, req_data)

    # Error interno en la toma de decisión (Blindaje/Fallback)
    with patch("run.choose_direction", side_effect=Exception("Crash del cerebro")):
        await run.process_move(ws, req_data)

    # Alias process_your_turn
    await run.process_your_turn(ws, req_data)


@pytest.mark.asyncio
async def test_play_loop_events():
    events = [
        json.dumps({'event': 'update_user_list'}),
        json.dumps({'event': 'challenge', 'data': {'challenge_id': 'ch1'}}),
        json.dumps({'event': 'your_turn', 'data': {'game_id': 'g1', 'turn_token': 't1', 'board': '|A |', 'side': 'A'}}),
        json.dumps({'event': 'game_over', 'data': {'game_id': 'g1'}}),
        json.dumps({'event': 'error', 'data': 'Servidor ocupado'}),
        KeyboardInterrupt()  # Forzar salida limpia del bucle play()
    ]

    ws = AsyncMock()
    ws.recv.side_effect = events
    await run.play(ws)


@pytest.mark.asyncio
async def test_play_loop_generic_exception():
    ws = AsyncMock()
    ws.recv.side_effect = Exception("Conexión perdida")
    await run.play(ws)


@pytest.mark.asyncio
async def test_start_loop():
    # Simular un error de conexión inicial seguido de una interrupción de teclado
    with patch("websockets.connect", side_effect=[Exception("Server down"), KeyboardInterrupt()]), \
         patch("time.sleep"):
        await run.start("fake_token")