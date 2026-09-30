import argparse

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description='MiniLang 调试器后端')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()
    app = create_app(args.data_dir)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
