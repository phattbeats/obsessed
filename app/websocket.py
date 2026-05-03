"""
WebSocket connection manager for Obsessed real-time game events.
One room = one game. All players in the same room receive broadcast events.
"""
from fastapi import WebSocket
from typing import dict

# room_code -> set of (player_id, websocket)
_rooms: dict[str, dict[str, WebSocket]] = {}


def get_room(room_code: str) -> dict[str, WebSocket]:
    if room_code not in _rooms:
        _rooms[room_code] = {}
    return _rooms[room_code]


async def connect(websocket: WebSocket, room_code: str, player_id: str):
    """Register a player's WebSocket in a game room."""
    await websocket.accept()
    room = get_room(room_code)
    room[player_id] = websocket
    await broadcast(room_code, {
        "type": "player_joined",
        "player_id": player_id,
        "player_count": len(room),
    })


async def disconnect(room_code: str, player_id: str):
    """Remove a player's WebSocket when they leave."""
    room = _rooms.get(room_code, {})
    if player_id in room:
        del room[player_id]
    if not room:
        del _rooms[room_code]


async def broadcast(room_code: str, message: dict):
    """Send a message to all players in a game room."""
    room = _rooms.get(room_code, {})
    msg_bytes = str(message).encode()
    dead = []
    for player_id, ws in room.items():
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(player_id)
    for pid in dead:
        await disconnect(room_code, pid)


async def send_to(room_code: str, player_id: str, message: dict):
    """Send a message to a specific player (e.g., your answer was correct)."""
    room = _rooms.get(room_code, {})
    ws = room.get(player_id)
    if ws:
        try:
            await ws.send_json(message)
        except Exception:
            await disconnect(room_code, player_id)