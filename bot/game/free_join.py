import json
import websockets
from bot.config import SKILL_VERSION

async def join_free_game(api_key):
    uri = "wss://cdn.moltyroyale.com/ws/join"
    headers = {
        "X-API-Key": api_key,
        "X-Version": SKILL_VERSION
    }

    try:
        async with websockets.connect(uri, additional_headers=headers) as ws:
            welcome_msg = await ws.recv()
            welcome_data = json.loads(welcome_msg)
            
            # Jika server membalas dengan welcome
            if welcome_data.get("type") == "welcome":
                hello_payload = {
                    "type": "hello",
                    "entryType": "free"
                }
                await ws.send(json.dumps(hello_payload))

                while True:
                    response_msg = await ws.recv()
                    response_data = json.loads(response_msg)
                    
                    if response_data.get("type") == "assigned" or response_data.get("status") == "assigned":
                        print(f"✅ Match Found! Game ID: {response_data.get('gameId')}")
                        return response_data, ws
                        
                    elif response_data.get("status") == "not_selected":
                        return {"status": "not_selected"}, None
                        
                    elif response_data.get("status") == "queued":
                        print("⏳ Masih mengantre di dalam server...")
            
            # [PERBAIKAN] Jika pesan pertama bukan "welcome" (misalnya error dari server)
            else:
                print(f"⚠️ Ditolak server sebelum masuk: {welcome_data}")
                return {"status": "error", "message": str(welcome_data)}, None

    except Exception as e:
        print(f"Error saat matchmaking: {e}")
        return {"status": "error", "message": str(e)}, None

    # [PERBAIKAN FINAL] Jaring pengaman mutlak di paling bawah fungsi
    return {"status": "error", "message": "Fungsi berhenti secara tidak terduga"}, None
