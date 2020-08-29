#!/usr/bin/env python3

import argparse
import asyncio
import logging
import os

from joycontrol import logging_default as log, utils
from joycontrol.socket_interface import ControllerSocketInterface
from joycontrol.controller import Controller
from joycontrol.controller_state import button_press, button_release
from joycontrol.memory import FlashMemory
from joycontrol.protocol import controller_protocol_factory
from joycontrol.server import create_hid_server

logger = logging.getLogger(__name__)

"""Emulates Switch controller. Opens joycontrol.command_line_interface to send button commands and more.

While running the cli, call "help" for an explanation of available commands.

Usage:
    run_controller_cli.py <controller> [--device_id | -d  <bluetooth_adapter_id>]
                                       [--spi_flash <spi_flash_memory_file>]
                                       [--reconnect_bt_addr | -r <console_bluetooth_address>]
                                       [--log | -l <communication_log_file>]
                                       [--nfc <nfc_data_file>]
    run_controller_cli.py -h | --help

Arguments:
    controller      Choose which controller to emulate. Either "JOYCON_R", "JOYCON_L" or "PRO_CONTROLLER"

Options:
    -d --device_id <bluetooth_adapter_id>   ID of the bluetooth adapter. Integer matching the digit in the hci* notation
                                            (e.g. hci0, hci1, ...) or Bluetooth mac address of the adapter in string
                                            notation (e.g. "FF:FF:FF:FF:FF:FF").
                                            Note: Selection of adapters may not work if the bluez "input" plugin is
                                            enabled.

    --spi_flash <spi_flash_memory_file>     Memory dump of a real Switch controller. Required for joystick emulation.
                                            Allows displaying of JoyCon colors.
                                            Memory dumps can be created using the dump_spi_flash.py script.

    -r --reconnect_bt_addr <console_bluetooth_address>  Previously connected Switch console Bluetooth address in string
                                                        notation (e.g. "FF:FF:FF:FF:FF:FF") for reconnection.
                                                        Does not require the "Change Grip/Order" menu to be opened,

    -l --log <communication_log_file>       Write hid communication (input reports and output reports) to a file.

    --nfc <nfc_data_file>                   Sets the nfc data of the controller to a given nfc dump upon initial
                                            connection.
"""

def ensure_valid_button(controller_state, *buttons):
    """
    Raise ValueError if any of the given buttons os not part of the controller state.
    :param controller_state:
    :param buttons: Any number of buttons to check (see ButtonState.get_available_buttons)
    """
    for button in buttons:
        if button not in controller_state.button_state.get_available_buttons():
            raise ValueError(f'Button {button} does not exist on {controller_state.get_controller()}')

def _register_commands_with_controller_state(controller_state, socket_interface):
    """
    Commands registered here can use the given controller state.
    The doc string of commands will be printed by the CLI when calling "help"
    :param cli:
    :param controller_state:
    """

    # Hold a button command
    async def hold(*args):
        """
        hold - Press and hold specified buttons

        Usage:
            hold <button>

        Example:
            hold a b
        """
        if not args:
            raise ValueError('"hold" command requires a button!')

        ensure_valid_button(controller_state, *args)

        logger.info("Holding %s" % " ".join(args))
        # wait until controller is fully connected
        await controller_state.connect()
        await button_press(controller_state, *args)

    socket_interface.add_command(hold.__name__, hold)

    # Release a button command
    async def release(*args):
        """
        release - Release specified buttons

        Usage:
            release <button>

        Example:
            release a b
        """
        if not args:
            raise ValueError('"release" command requires a button!')

        ensure_valid_button(controller_state, *args)

        logger.info("Releasing %s" % " ".join(args))
        # wait until controller is fully connected
        await controller_state.connect()
        await button_release(controller_state, *args)

    socket_interface.add_command(release.__name__, release)

    async def clear_nfc(sec=3):
        await asyncio.sleep(sec)
        logger.info("Clearing NFC content")
        controller_state.set_nfc(None)

    async def nfc(file_path, sec=3):
        if controller_state.get_controller() == Controller.JOYCON_L:
            raise ValueError('NFC content cannot be set for JOYCON_L')
        elif not file_path:
            raise ValueError('"nfc" command requires file path to an nfc dump as argument!')
        else:
            # load and unload amiibo
            await clear_nfc(0)
            _loop = asyncio.get_event_loop()
            with open(file_path, 'rb') as nfc_file:
                content = await _loop.run_in_executor(None, nfc_file.read)
                logger.info("Setting NFC content: %s" % file_path)
                controller_state.set_nfc(content)
                asyncio.ensure_future(clear_nfc(sec))

    socket_interface.add_command(nfc.__name__, nfc)


async def _main(args):
    # parse the spi flash
    if args.spi_flash:
        with open(args.spi_flash, 'rb') as spi_flash_file:
            spi_flash = FlashMemory(spi_flash_file.read())
    else:
        # Create memory containing default controller stick calibration
        spi_flash = FlashMemory()

    # Get controller name to emulate from arguments
    controller = Controller.from_arg(args.controller)

    with utils.get_output(path=args.log, default=None) as capture_file:
        # prepare the the emulated controller
        factory = controller_protocol_factory(controller, spi_flash=spi_flash)
        ctl_psm, itr_psm = 17, 19

        if args.bt_addr_file is not None:
            bt_addr = open(args.bt_addr_file, "r").read()
            if bt_addr == "ANY":
                bt_addr = None
        else:
            bt_addr = args.reconnect_bt_addr

        transport, protocol = await create_hid_server(factory, reconnect_bt_addr=bt_addr,
                                                      ctl_psm=ctl_psm,
                                                      itr_psm=itr_psm, capture_file=capture_file,
                                                      device_id=args.device_id)

        controller_state = protocol.get_controller_state()

        # Create socket interface and add some extra commands
        socket_interface = ControllerSocketInterface(args.socket, controller_state)
        _register_commands_with_controller_state(controller_state, socket_interface)

        # run the socket_interface
        await socket_interface.start_server()
        return socket_interface, transport


if __name__ == '__main__':
    # check if root
    if not os.geteuid() == 0:
        raise PermissionError('Script must be run as root!')

    # setup logging
    #log.configure(console_level=logging.ERROR)
    log.configure()

    parser = argparse.ArgumentParser()
    parser.add_argument('controller', help='JOYCON_R, JOYCON_L or PRO_CONTROLLER')
    parser.add_argument('-s', '--socket', default="./joycontrol.socket")
    parser.add_argument('-l', '--log')
    parser.add_argument('-d', '--device_id')
    parser.add_argument('--spi_flash')
    parser.add_argument('-r', '--reconnect_bt_addr', type=str, default=None,
                        help='The Switch console Bluetooth address, for reconnecting as an already paired controller')
    parser.add_argument('--bt_addr_file')
    parser.add_argument('--nfc', type=str, default=None)
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    socket_interface, transport = loop.run_until_complete(_main(args))
    try:
        loop.run_forever()
    finally:
        logger.info('Stopping communication...')
        socket_interface.cleanup()
        loop.run_until_complete(transport.close())
