import re
import socket
from abc import abstractmethod, ABC
from argparse import ArgumentParser
from typing import Dict, List


class Event:

    def __init__(self):
        self.events: Dict[str, List[callable]] = {}

    def post_event(self, event_name: str, *args, **kwargs):
        """

        :param event_name: The name of the event to trigger
        :param args: A list of additional arguments to pass to the callback's parameters
        :param kwargs: A list of additional arguments to pass to the callback's parameters
        :return:
        """
        print("EVENT: %s" % event_name)
        if event_name in self.events:
            for cb in self.events[event_name]:
                cb(*args, **kwargs)

    def add_event_listener(self, cb: callable, *events: str):
        print("Registering Event Listener for events: %s" % str(events))
        for name in events:
            if name not in self.events:
                self.events[name] = []
            self.events[name].append(cb)

    def remove_event_listener(self, cb: callable, *events: str):
        if len(events) > 0:
            print("Removing Event Listener for events: %s" % str(events))
            for name in events:
                if name in self.events:
                    self.events[name].remove(cb)
        else:
            print("Removing Event Listener")
            for callables in self.events.values():
                callables.remove(cb)


class SocketConnection(Event, ABC):
    def __init__(self, addr, port):
        super().__init__()
        self.addr = addr
        self.port = port
        self.socket: socket.socket = None
        self.connect_attempts = 5
        self.timeout = 15

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.post_event('before_connect')
        for i in range(0, self.connect_attempts):
            try:
                self.socket.connect((self.addr, self.port))
                self.socket.settimeout(0)
                self.post_event('after_connect')
                return True
            except (socket.timeout, ConnectionRefusedError, ConnectionAbortedError):
                print("Socket timeout occurred. Attempt %i out of %i" % (i, self.connect_attempts))
                self.post_event('before_connect_retry', i)
        self.post_event('connect_fail')
        return False

    def disconnect(self):
        if self.socket is None:
            return False
        self.post_event('before_disconnect')
        self.socket.close()
        self.socket = None
        self.post_event('after_disconnect')
        return True

    def update(self):
        buffer = bytes()
        self.socket.settimeout(0)

        while True:
            try:
                buffer += self.socket.recv(4096)
            except OSError:
                break
        if len(buffer) > 0:
            self.post_event('new_data', buffer)

    def _test_ack(self):
        self.post_event('before_ack')
        if not self.perform_ack():
            self.post_event('timeout_ack')
            self.disconnect()
            return False
        else:
            self.post_event('after_ack')
            return True

    def update_forever(self):
        self.socket.settimeout(self.timeout)

        while True:
            try:
                buffer = self.socket.recv(4096)
                if len(buffer) > 0:
                    self.post_event('new_data', buffer)
                elif not self._test_ack():
                    return False
            except OSError:
                if not self._test_ack():
                    return False

    @abstractmethod
    def perform_ack(self):
        pass


class Block:
    def __init__(self, byte_arr: bytes):
        # Create a set of lines from the decoded version of this
        lines = byte_arr.decode('ascii').split('\n')

        # The title PROTOCOL PREAMBLE, VIDEOHUB DEVICE, etc
        self._title = lines[0][:-1]
        self._data = {}

        # Go through the not header and deal with it all
        for i in range(1, len(lines)):
            line = lines[i]
            kv_pair = line.split(":")

            if len(kv_pair) > 1:
                self._data[kv_pair[0]] = kv_pair[1][1:]
            else:
                # This is a non colon seperated section
                kv_pair = line.split(" ", 1)

                # Try and get the key into a number.
                # I don't think it's in-spec for this to ever not be a number
                try:
                    key = int(kv_pair[0])
                except ValueError:
                    key = kv_pair[0]

                # Dont' turn value into a number because an output title of "1" would become a number, but it's not
                self._data[key] = kv_pair[1]

    @property
    def title(self):
        return self._title

    @property
    def data(self):
        return self._data

    def __str__(self):
        return"Block: %s" % str({
            "title":  self._title,
            "data": self._data
        })


class Command:
    def __init__(self, title=None, data=None, is_colon=False, success: callable = None, failure: callable = None):
        self.title = title
        self.data = data or {}
        self.is_colon = is_colon
        self.success = success or (lambda: None)
        self.failure = failure or (lambda: None)

    def raw_data(self) -> bytes:
        text = self.title + ":\n"
        for key, val in self.data.items():
            if self.is_colon:
                text += "%s: %s\n" % (key, val)
            else:
                text += "%s %s\n" % (key, val)
        return bytes(text + "\n\n", 'ascii')

    def on_success(self, callback: callable):
        self.success = callback
        return self

    def on_failure(self, callback: callable):
        self.failure = callback
        return self


class VideoHubConnection(SocketConnection):
    def perform_ack(self):
        self.socket.send(b'PING:\n\n')
        try:
            data = self.socket.recv(1024)
        except OSError:
            return False
        print(data)
        return len(data) > 0

    def __init__(self, addr, port=9990):
        super().__init__(addr, port)
        self.add_event_listener(self.on_data, 'new_data')
        self.add_event_listener(self.on_command_ack, 'command_ack')
        self.buffer = bytes()

        self.last_command: Command = None
        self.command_queue: List[Command] = []

    def send_command(self, command: Command):
        self.command_queue.append(command)
        self.push_command_queue()

    def push_command_queue(self):
        if self.last_command is not None:
            return
        try:
            command = self.command_queue.pop()
        except IndexError:
            return
        raw = command.raw_data()
        print("SEND: %s" % raw)
        self.last_command = command
        self.socket.send(raw)

    def on_command_ack(self, result):
        if self.last_command is not None:
            if result:
                self.last_command.success()
            else:
                self.last_command.failure()
        else:
            print('ERROR: ACK to non-existent command!')
        self.last_command = None
        self.push_command_queue()

    def on_data(self, data: bytes):
        self.buffer += data
        while b'\n\n' in self.buffer:
            block = self.buffer[0:self.buffer.find(b'\n\n')]
            if block == b'ACK' or block == b'NAK':
                self.post_event('command_ack', block == b'ACK')
            else:
                self.post_event('new_block', Block(block))
            self.buffer = self.buffer[self.buffer.find(b'\n\n')+2:]


class VideoHubAPI(VideoHubConnection):
    COLON_BLOCKS = [
        'PROTOCOL PREAMBLE',
        'VIDEOHUB DEVICE'
    ]
    INPUT_LABEL_TITLE = "INPUT LABELS"
    OUTPUT_LABEL_TITLE = "OUTPUT LABELS"
    ROUTE_TITLE = "VIDEO OUTPUT ROUTING"
    LOCK_TITLE = "VIDEO OUTPUT LOCKS"

    def __init__(self, addr, port=9990):
        super().__init__(addr, port)
        self.info = {}
        self.add_event_listener(self._on_new_block, 'new_block')
        self.input_labels = {}
        self.output_labels = {}
        self.routes = {}
        self.locks = {}

    def _on_new_block(self, block: Block):
        if block.title in VideoHubAPI.COLON_BLOCKS:
            self.info = {
                **self.info,
                **block.data
            }
        elif block.title == VideoHubAPI.INPUT_LABEL_TITLE:
            self.input_labels = {
                **self.input_labels,
                **block.data
            }
        elif block.title == VideoHubAPI.OUTPUT_LABEL_TITLE:
            self.output_labels = {
                **self.output_labels,
                **block.data
            }
        elif block.title == VideoHubAPI.ROUTE_TITLE:
            self.routes = {
                **self.routes,
                **{key: int(value) for key, value in block.data.items()}
            }
        elif block.title == VideoHubAPI.LOCK_TITLE:
            self.locks = {
                **self.locks,
                **{key: True if value == "L" else False for key, value in block.data.items()}
            }

    def route_individual(self, *args):
        data = {}
        for i in range(0, len(args), 2):
            data[i] = data[i+1]
        command = Command(self.ROUTE_TITLE, data)
        self.send_command(command)
        return command

    def route(self, data):
        command = Command(self.ROUTE_TITLE, data)
        self.send_command(command)
        return command

    def set_input_labels(self, labels):
        command = Command(self.INPUT_LABEL_TITLE, labels)
        self.send_command(command)
        return command

    def set_output_labels(self, labels):
        command = Command(self.OUTPUT_LABEL_TITLE, labels)
        self.send_command(command)
        return command

    def set_locks(self, locks):
        locks = {key: "L" if value else "U" for key, value in locks.items()}
        command = Command(self.LOCK_TITLE, locks)
        self.send_command(command)
        return command

    def enable_locks(self, *outputs):
        self.set_locks({key: 'L' for key in outputs})

    def disable_locks(self, *outputs):
        self.set_locks({key: 'U' for key in outputs})


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('host', type=str)
    parser.add_argument('--port', type=int, default=9990)

    parser.add_argument('--route', action='append', nargs='+')
    parser.add_argument('--lock', type=int, action='append', nargs='+')
    parser.add_argument('--unlock',  type=int, action='append', nargs='+')
    parser.add_argument('--label-input', type=int, action='append', nargs='+')
    parser.add_argument('--label-output', type=int, action='append', nargs='+')

    parsed = parser.parse_args()
    conn = VideoHubAPI(parsed.host, parsed.port)
    if not conn.connect():
        print('ERROR: Failed to connect to device.')
        exit(1)

    # Locks
    locks = {}
    if parsed.lock:
        locks = {
            **locks,
            **{key: True for key in parsed.locks}
        }
    elif parsed.unlock:
        locks = {
            **locks,
            **{key: False for key in parsed.locks}
        }
    if len(locks) > 0:
        print("Setting locks: %s" % str(locks))
        conn.set_locks(locks)

    # Routes
    if parsed.route:
        print("Routing: %s")
        for i in range(0, len(parsed.route), 2):
            print("Destination %d to %d" % (parsed.route[i], parsed.route[i+1]))
        conn.route_individual(*[re.sub("[^0-9]", "", key) for key in parsed.routes])

