import uuid
import asyncio
import json

import websockets


async def async_input(prompt: str = "") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)

async def receive_messages(ws):
    try:
        async for message in ws:
            print(f"收到消息: {message}")
    except websockets.ConnectionClosed:
        pass


async def main():
    uri = "ws://localhost:3001"  # 替换为你的 LLOneBot WebSocket 正向地址
    group_id = await async_input("输入群号: ")
    
    async with websockets.connect(uri) as websocket:
        asyncio.create_task(receive_messages(websocket))
        while True:
            message = await async_input("输入消息内容: ")
            echo = str(uuid.uuid4())
            data = {
                "action": "send_group_msg",
                "params": {
                    "group_id": int(group_id),
                    "message": {
                        "type": "text",
                        "data": {
                            "text": message
                        }
                    }
                },
                'echo': echo
            }
            await websocket.send(json.dumps(data))

asyncio.run(main())