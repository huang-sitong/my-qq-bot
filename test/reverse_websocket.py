import asyncio
import json
import uuid

import websockets

websocket_clients = set()


async def handle_connection(websocket):
    print("\n=== LLOneBot 已连接 ===\n")
    websocket_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            post_type = data.get("post_type", "")

            # 格式化显示不同事件类型
            if post_type == "meta_event":
                meta_type = data.get("meta_event_type", "")
                print(f"[元事件] {meta_type} - {data.get('sub_type', '')}")
                print(f"  机器人: {data.get('self_id')}")
            elif post_type == "message":
                msg_type = data.get("message_type", "")
                user_id = data.get("user_id", "")
                nickname = data.get("sender", {}).get("nickname", "")
                raw_msg = data.get("raw_message", "")
                group_id = data.get("group_id", "")
                if msg_type == "group":
                    print(f"[群消息] {nickname}({user_id})@{group_id}: {raw_msg}")
                else:
                    print(f"[私聊] {nickname}({user_id}): {raw_msg}")
            elif post_type == "notice":
                notice_type = data.get("notice_type", "")
                print(f"[通知] {notice_type}")
            elif post_type == "request":
                request_type = data.get("request_type", "")
                print(f"[请求] {request_type}")
            else:
                # API 调用响应
                echo = data.get("echo", "")
                status = data.get("status", "")
                if echo:
                    print(f"[API响应] echo={echo[:8]}... status={status}")
                else:
                    print(f"[其他] {data}")
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[错误] {e}")
    finally:
        websocket_clients.discard(websocket)
        print("\n=== LLOneBot 已断开 ===\n")


async def start_server():
    async with websockets.serve(handle_connection, "localhost", 8765):
        print("WebSocket 服务器已启动，监听 ws://localhost:8765")
        print("等待 LLOneBot 连接...\n")
        await asyncio.Future()


async def async_input(prompt: str = "") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)


async def main():
    # 先启动服务器再等输入
    asyncio.create_task(start_server())
    await asyncio.sleep(0.5)

    group_id = await async_input("输入群号: ")
    group_id = group_id.strip()

    print("输入消息发送到群，输入 /quit 退出\n")

    while True:
        message = await async_input("> ")
        if not message.strip():
            continue
        if message.strip() == "/quit":
            print("退出")
            break

        echo = str(uuid.uuid4())
        data = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": [
                    {"type": "text", "data": {"text": message}}
                ],
            },
            "echo": echo,
        }
        for ws in list(websocket_clients):
            await ws.send(json.dumps(data))


asyncio.run(main())
