import logging
import os
from asyncio import start_unix_server

from joycontrol.controller_state import ControllerState
from joycontrol.transport import NotConnectedError

logger = logging.getLogger(__name__)


class SocketInterface:
    def __init__(self):
        self.commands = {}

    def add_command(self, name, command):
        if name in self.commands:
            raise ValueError(f'Command {name} already registered.')
        self.commands[name] = command


class ControllerSocketInterface(SocketInterface):
    def __init__(self, socket_location, controller_state: ControllerState):
        super().__init__()
        self.server = None
        self.socket_location = socket_location
        self.controller_state = controller_state

    @staticmethod
    def _set_stick(stick, direction, value):
        if direction == 'center':
            stick.set_center()
        elif direction == 'up':
            stick.set_up()
        elif direction == 'down':
            stick.set_down()
        elif direction == 'left':
            stick.set_left()
        elif direction == 'right':
            stick.set_right()
        elif direction in ('h', 'horizontal'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_h(val)
        elif direction in ('v', 'vertical'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_v(val)
        else:
            raise ValueError(f'Unexpected argument "{direction}"')

        return f'{stick.__class__.__name__} was set to ({stick.get_h()}, {stick.get_v()}).'

    async def cmd_stick(self, side, direction, value=None):
        """
        stick - Command to set stick positions.
        :param side: 'l', 'left' for left control stick; 'r', 'right' for right control stick
        :param direction: 'center', 'up', 'down', 'left', 'right';
                          'h', 'horizontal' or 'v', 'vertical' to set the value directly to the "value" argument
        :param value: horizontal or vertical value
        """
        if side in ('l', 'left'):
            stick = self.controller_state.l_stick_state
            return ControllerSocketInterface._set_stick(stick, direction, value)
        elif side in ('r', 'right'):
            stick = self.controller_state.r_stick_state
            return ControllerSocketInterface._set_stick(stick, direction, value)
        else:
            raise ValueError('Value of side must be "l", "left" or "r", "right"')

    async def start_server(self):
        try:
            os.unlink(self.socket_location)
        except OSError:
            if os.path.exists(self.socket_location):
                raise

        self.server = await start_unix_server(self.handle_client, self.socket_location)
        os.chmod(self.socket_location, 0o777)

    async def handle_client(self, reader, writer):
        while True:
            line = await reader.readuntil()
            await self.handle_line(line.decode("utf-8").rstrip("\n"))

    def cleanup(self):
        self.server.close()

        try:
            os.unlink(self.socket_location)
        except OSError:
            raise

    async def handle_line(self, line):
        if not line:
            return

        cmd = ""
        args = []

        if line.startswith("btn:"):
            action, button, pressed = line.split(":")
            if pressed == "true":
                cmd = "hold"
                args = [button]
            else:
                cmd = "release"
                args = [button]
        elif line.startswith("stick:"):
            action, stick, direction, pressed = line.split(":")
            cmd = "stick"
            if pressed == "true":
                args = [stick, direction]
            else:
                args = [stick, "center"]
        elif line.startswith("nfc:"):
            action, file_path = line.split(":", 1)
            cmd = "nfc"
            args = [file_path]

        if hasattr(self, f'cmd_{cmd}'):
            try:
                result = await getattr(self, f'cmd_{cmd}')(*args)
                if result:
                    print(result)
            except Exception as e:
                print(e)
        elif cmd in self.commands:
            try:
                result = await self.commands[cmd](*args)
                if result:
                    print(result)
            except Exception as e:
                print(e)
        else:
            print('command', cmd, 'not found, call help for help.')

        try:
            await self.controller_state.send()
        except NotConnectedError:
            logger.info('Connection was lost.')
            raise
