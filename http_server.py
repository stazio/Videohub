import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from videohub import VideoHubAPI

api = VideoHubAPI(sys.argv[1], 9990)

local_settings = {
    'colors': {}
}
if os.path.isfile('settings.conf'):
    with open('settings.conf') as f:
        local_settings = json.loads(f.read())


def save_settings():
    with open('settings.conf', 'w') as f:
        f.write(json.dumps(local_settings))


class HTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.send_response(200, 'OK')
            self.send_header('Content-type', 'text/html charset=utf-8;')
            self.end_headers()

            with open('server/index.html', 'rb') as f:
                self.wfile.write(f.read())

        elif self.path.find('/api?action=status') == 0:
            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json charset=utf-8;')
            self.end_headers()
            self.wfile.write(bytes(json.dumps({
                'meta': {
                    'name': api.info['Model name'],
                    'size': len(api.output_labels),
                },
                'inputs': api.input_labels,
                'outputs': api.output_labels,
                'routes': api.routes,
                **local_settings
            }), 'utf-8'))
        else:
            self.send_header('Content-type', 'text/plain')
            self.send_response(404, 'Not Found')
            self.end_headers()
            self.wfile.write(b'Page not found')

    def do_POST(self):
        if self.path.find("/api?action=route") == 0:
            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            datainc = json.loads(self.rfile.read(int(self.headers['content-length'])).decode('utf-8'))
            api.route({datainc['dest']: datainc['src']})

        elif self.path.find("/api?action=settings") == 0:
            self.send_response(200, 'OK')
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            datainc = json.loads(self.rfile.read(int(self.headers['content-length'])).decode('utf-8'))
            if 'colors' in datainc:
                local_settings['colors'] = {
                    **local_settings['colors'],
                    **datainc['colors']
                }
                save_settings()
        else:
            self.send_response(400, 'Not found')
            self.send_header('Content-type', 'application/json')
            self.end_headers()


def api_target():
    api.update_forever()
    print("API EXIT")
    inst.server_close()


if __name__ == "__main__":
    api.connect()
    threading.Thread(target=api_target).start()
    inst = ThreadingHTTPServer(('localhost', 8080), HTTPRequestHandler)
    inst.serve_forever()
