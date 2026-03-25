from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify

app = Flask(__name__)


def _probe_mysql() -> dict[str, Any]:
    try:
        import pymysql  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency check
        return {"status": "error", "error": f"pymysql import failed: {exc}"}

    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "root")
    database = os.getenv("DB_NAME", "app")

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=2,
            read_timeout=2,
            write_timeout=2,
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
        conn.close()
        return {"status": "ok", "result": row[0] if row else None}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _probe_redis() -> dict[str, Any]:
    try:
        import redis  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime dependency check
        return {"status": "error", "error": f"redis import failed: {exc}"}

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))

    try:
        client = redis.Redis(host=host, port=port, socket_connect_timeout=2, socket_timeout=2)
        pong = client.ping()
        return {"status": "ok", "result": bool(pong)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok", "service": "sample-workspace-api"})


@app.get("/deps")
def deps() -> Any:
    return jsonify(
        {
            "mysql": _probe_mysql(),
            "redis": _probe_redis(),
            "env": {
                "DB_HOST": os.getenv("DB_HOST"),
                "DB_PORT": os.getenv("DB_PORT"),
                "DB_NAME": os.getenv("DB_NAME"),
                "REDIS_HOST": os.getenv("REDIS_HOST"),
                "REDIS_PORT": os.getenv("REDIS_PORT"),
            },
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
