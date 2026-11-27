# Copyright 2016-2021 Alex Yatskov
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import json
import select
import socket
import time

from . import util

MAX_HEADER_SIZE = 64 * 1024  # 64 KiB
MAX_REQUEST_SIZE = 64 * 1024 * 1024  # 64 MiB
CLIENT_IDLE_TIMEOUT_S = 10.0
RECV_SIZE = 65536


class _HttpParseError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _sanitize_header_value(value: str) -> str:
    return value.replace("\r", "").replace("\n", "")


def _validate_request(params: object) -> None:
    if not isinstance(params, dict):
        raise ValueError("request body must be a JSON object")
    action = params.get("action")
    if not isinstance(action, str) or len(action) < 1:
        raise ValueError("'action' must be a non-empty string")
    if "version" in params:
        version = params["version"]
        if type(version) is not int:
            raise ValueError("'version' must be an integer")
    if "params" in params and not isinstance(params["params"], dict):
        raise ValueError("'params' must be an object")


#
# WebRequest
#


class WebRequest:
    def __init__(self, method, headers, body):
        self.method = method
        self.headers = headers
        self.body = body


#
# WebClient
#


class WebClient:
    def __init__(self, sock, handler):
        self.sock = sock
        self.handler = handler
        self.readBuff = b""
        self.writeBuff = b""
        self.lastActivity = time.monotonic()

    def advance(self, recvSize=RECV_SIZE):
        if self.sock is None:
            return False

        now = time.monotonic()
        if now - self.lastActivity > CLIENT_IDLE_TIMEOUT_S:
            self.close()
            return False

        try:
            watch_write = [self.sock] if self.writeBuff else []
            rlist, wlist = select.select([self.sock], watch_write, [], 0)[:2]
        except OSError:
            self.close()
            return False

        if rlist and not self.writeBuff:
            try:
                msg = self.sock.recv(recvSize)
            except BlockingIOError:
                return True
            except OSError:
                self.close()
                return False

            if not msg:
                self.close()
                return False

            self.lastActivity = now
            self.readBuff += msg

            try:
                req, length = self.parseRequest(self.readBuff)
            except _HttpParseError as e:
                self.readBuff = b""
                self.writeBuff = self._errorResponse(e.status, e.message)
                return self._trySend()

            if req is not None:
                self.readBuff = self.readBuff[length:]
                try:
                    self.writeBuff += self.handler(req)
                except Exception as e:
                    self.writeBuff = self._errorResponse("500 Internal Server Error", str(e))
                return self._trySend()

        if wlist and self.writeBuff:
            return self._trySend()

        return True

    def _trySend(self):
        if self.sock is None or not self.writeBuff:
            return False
        try:
            length = self.sock.send(self.writeBuff)
        except BlockingIOError:
            return True
        except OSError:
            self.close()
            return False

        self.lastActivity = time.monotonic()
        self.writeBuff = self.writeBuff[length:]
        if not self.writeBuff:
            self.close()
            return False
        return True

    def _errorResponse(self, status: str, message: str) -> bytes:
        body = json.dumps({"result": None, "error": message}).encode("utf-8")
        headers = [
            [f"HTTP/1.1 {status}", None],
            ["Content-Type", "application/json"],
            ["Content-Length", str(len(body))],
            ["Connection", "close"],
        ]
        return WebServer.buildResponse(headers, body)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

        self.readBuff = b""
        self.writeBuff = b""

    def parseRequest(self, data):
        parts = data.split(b"\r\n\r\n", 1)
        if len(parts) == 1:
            if len(data) > MAX_HEADER_SIZE:
                raise _HttpParseError("413 Payload Too Large", "HTTP headers too large")
            return None, 0

        if len(parts[0]) > MAX_HEADER_SIZE:
            raise _HttpParseError("413 Payload Too Large", "HTTP headers too large")

        lines = parts[0].split(b"\r\n")
        method = None

        if lines:
            request_line_parts = lines[0].split(b" ")
            method = request_line_parts[0].upper() if request_line_parts else None

        headers = {}
        for line in lines[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            headers[key.strip().lower()] = value.strip()

        headerLength = len(parts[0]) + 4
        raw_cl = headers.get(b"content-length")
        if raw_cl is None:
            bodyLength = 0
        else:
            try:
                bodyLength = int(raw_cl)
            except ValueError as e:
                raise _HttpParseError("400 Bad Request", "invalid Content-Length") from e
            if bodyLength < 0:
                raise _HttpParseError("400 Bad Request", "negative Content-Length")

        totalLength = headerLength + bodyLength
        if totalLength > MAX_REQUEST_SIZE:
            raise _HttpParseError("413 Payload Too Large", "request body too large")

        if totalLength > len(data):
            return None, 0

        body = data[headerLength:totalLength]
        return WebRequest(method, headers, body), totalLength


#
# WebServer
#


class WebServer:
    def __init__(self, handler):
        self.handler = handler
        self.clients = []
        self.sock = None

    def advance(self):
        if self.sock is not None:
            self.acceptClients()
            self.advanceClients()

    def acceptClients(self):
        # Drain all pending accepts each tick.
        # The original single-accept behavior can overflow backlog under
        # bursty callers (e.g., parallel sync workers), causing intermittent
        # connection failures.
        while True:
            try:
                rlist = select.select([self.sock], [], [], 0)[0]
                if not rlist:
                    return

                clientSock = self.sock.accept()[0]
                if clientSock is None:
                    return

                clientSock.setblocking(False)
                self.clients.append(WebClient(clientSock, self.handlerWrapper))
            except (BlockingIOError, OSError):
                return

    def advanceClients(self):
        alive = []
        for client in self.clients:
            try:
                if client.advance():
                    alive.append(client)
            except Exception:
                client.close()
        self.clients = alive

    def listen(self):
        self.close()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        self.sock.bind((util.setting("webBindAddress"), util.setting("webBindPort")))
        self.sock.listen(util.setting("webBacklog"))

    def handlerWrapper(self, req):
        allowed, corsOrigin = self.allowOrigin(req)
        corsOrigin = _sanitize_header_value(corsOrigin)

        if req.method == b"OPTIONS":
            body = b""
            headers = self.buildHeaders(corsOrigin, body)

            if req.headers.get(b"access-control-request-private-network") == b"true":
                # include this header so that if a public origin is included in the whitelist,
                # then browsers won't fail requests due to the private network access check
                headers.append(["Access-Control-Allow-Private-Network", "true"])

            return self.buildResponse(headers, body)

        try:
            params = json.loads(req.body.decode("utf-8"))
            _validate_request(params)
        except ValueError as e:
            if allowed:
                if len(req.body) == 0:
                    body = f"AnkiConnect v.{util.setting('apiVersion')}".encode()
                else:
                    reply = format_exception_reply(util.setting("apiVersion"), e)
                    body = json.dumps(reply).encode("utf-8")
                headers = self.buildHeaders(corsOrigin, body)
                return self.buildResponse(headers, body)
            else:
                params = {}  # trigger the 403 response below

        if allowed or params.get("action", "") == "requestPermission":
            if params.get("action", "") == "requestPermission":
                origin_str = self._decodeOrigin(req) or ""
                params["params"] = params.get("params", {})
                params["params"]["allowed"] = allowed
                params["params"]["origin"] = origin_str
                if not allowed:
                    corsOrigin = _sanitize_header_value(origin_str)

            body = json.dumps(self.handler(params)).encode("utf-8")
            headers = self.buildHeaders(corsOrigin, body)
        else:
            headers = [
                ["HTTP/1.1 403 Forbidden", None],
                ["Access-Control-Allow-Origin", corsOrigin],
                ["Access-Control-Allow-Headers", "*"],
                # The server closes every connection after one reply (WebClient.advance);
                # this tells the client, so it never reuses one the server has dropped.
                ["Connection", "close"],
            ]
            body = b""

        return self.buildResponse(headers, body)

    @staticmethod
    def _decodeOrigin(req) -> str | None:
        raw = req.headers.get(b"origin")
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def allowOrigin(self, req):
        # handle multiple cors origins by checking the 'origin'-header against the allowed origin list from the config
        webCorsOriginList = util.setting("webCorsOriginList")

        allowed = False
        corsOrigin = "http://localhost"
        allowAllCors = "*" in webCorsOriginList  # allow CORS for all domains

        if allowAllCors:
            corsOrigin = "*"
            allowed = True
        elif b"origin" in req.headers:
            originStr = self._decodeOrigin(req)
            if originStr is None:
                return False, corsOrigin
            if originStr in webCorsOriginList:
                corsOrigin = originStr
                allowed = True
            elif "http://localhost" in webCorsOriginList and (
                originStr == "https://localhost"
                or originStr.startswith("http://localhost:")
                or originStr.startswith("https://localhost:")
                or originStr == "http://127.0.0.1"
                or originStr == "https://127.0.0.1"  # allow 127.0.0.1 if localhost allowed
                or originStr.startswith("http://127.0.0.1:")
                or originStr.startswith("https://127.0.0.1:")
            ):
                corsOrigin = originStr
                allowed = True
        else:
            allowed = True

        return allowed, corsOrigin

    def buildHeaders(self, corsOrigin, body):
        return [
            ["HTTP/1.1 200 OK", None],
            ["Content-Type", "application/json"],
            ["Access-Control-Allow-Origin", _sanitize_header_value(corsOrigin)],
            ["Access-Control-Allow-Headers", "*"],
            ["Content-Length", str(len(body))],
            # The server closes every connection after one reply (WebClient.advance);
            # this tells the client, so it never reuses one the server has dropped.
            ["Connection", "close"],
        ]

    @staticmethod
    def buildResponse(headers, body):
        resp = b""
        for key, value in headers:
            if value is None:
                resp += f"{key}\r\n".encode()
            else:
                resp += f"{key}: {value}\r\n".encode()

        resp += b"\r\n"
        resp += body
        return resp

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

        for client in self.clients:
            client.close()

        self.clients = []


def format_success_reply(api_version, result):
    if api_version <= 4:
        return result
    else:
        return {"result": result, "error": None}


def format_exception_reply(_api_version, exception):
    return {"result": None, "error": str(exception)}
