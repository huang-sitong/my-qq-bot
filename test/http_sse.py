import httpx
import asyncio

async def get_data():
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream('GET', 'http://localhost:3000/_events') as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    data = line.split("data:", 1)[1]
                    print(data)



asyncio.run(get_data())