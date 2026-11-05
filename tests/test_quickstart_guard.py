"""重复双击启动时，不能报一个看不懂的「端口被占用」。

quickstart 在真正拉起 uvicorn 之前先判断两件事：端口上是不是我们自己的
服务（是就直接开浏览器），以及端口是不是被别的程序占了（给出换端口的
命令）。这里用本机 socket / 临时 HTTP 服务把这两个判断钉住。
"""

from __future__ import annotations

import http.server
import socket
import threading

from scripts import quickstart


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):  # silence the test output
        return


def test_port_in_use_sees_a_listening_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert quickstart.port_in_use("127.0.0.1", port) is True
    assert quickstart.port_in_use("127.0.0.1", port) is False


def test_site_already_running_requires_http_200():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        assert quickstart.site_already_running("127.0.0.1", port) is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_site_already_running_is_false_when_nothing_listens():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert quickstart.site_already_running("127.0.0.1", free_port) is False
