"""QQ bot 薄入口。

装配逻辑见 ``bot.package.core.boot.create_app``；本文件只负责启动/停止。
"""

import asyncio

from bot.package.core.boot import create_app


async def main():
    app = await create_app()
    try:
        await app.start()
        await app.run()
    except KeyboardInterrupt:
        pass
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
