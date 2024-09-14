# SPDX-License-Identifier: GPL-2.0-or-later

"""
Monitor audio mute events from the sound system on the host to check for mute
state on active or default sound devices.

This is useful for setting the mute LED on keyboards or other devices which
have them and for when the volume is muted/set to zero outside of the keyboard.
"""
import logging
import asyncio
import threading

from libpulse.libpulse import (LibPulse, PA_SUBSCRIPTION_MASK_SINK,
                               PA_SUBSCRIPTION_MASK_SERVER, PulseEvent, LibPulseError)


MONITOR_THREAD_TIMEOUT = 0.2  # seconds


class MuteMonitor(object):
    """
    Simple class for monitoring mute state.
    """

    def __init__(self, mute_cb):
        """
        :param mute_cb: Callback function for applying mute state to be invoked
            on a change in the mute state.
        :type: Callable[[bool], Any]
        """
        self._logger = logging.getLogger('razer.mute')
        self._logger.info("Initialising Audio Mute Monitor")

        self._mute_cb = mute_cb
        self._monitor_thread = None
        self._mute_state = None

        self._logger.info("Using libpulse for pulseaudio/pipewire monitoring")

        self._monitoring = False
        self.monitoring = True

    @property
    def monitoring(self):
        """
        Monitoring property, if true then muting will be actioned.

        :return: If monitoring
        :rtype: bool
        """
        return self._monitoring

    @monitoring.setter
    def monitoring(self, value):
        """
        Monitoring property setter, if true then muting will be actioned.

        :param value: If monitoring
        :type: bool
        """
        if value and not self._monitoring:
            self._logger.debug("Starting mute monitoring")
            self._monitor_thread = threading.Thread(target=self._start_monitor_mute)
            self._monitoring = True
            self._monitor_thread.start()

        elif not value and self._monitoring:
            self._logger.debug("Stopping mute monitoring")
            self._monitoring = False
            self._monitor_thread.join(MONITOR_THREAD_TIMEOUT)
            self._logger.debug("Stopped mute monitoring thread")

    def stop(self):
        """
        Stop mute monitoring.
        """
        self.monitoring = False

    @property
    def mute_state(self):
        """
        Mute state property, if true then the mute state is set, mute state is
        unset otherwise.
        """
        return self._mute_state

    @mute_state.setter
    def mute_state(self, mute_state):
        """
        Mute state property setter, if true then the mute state is set, mute
        state is unset otherwise.

        :param mute_state: State to set
        :type: bool
        """
        self.set_mute_state(mute_state)

    def set_mute_state(self, mute_state):
        """
        Wrapped by method `mute_state`, similarly sets mute state.

        :param mute_state: State to set
        :type: bool
        """
        if (self._mute_state != mute_state):
            self._mute_cb(mute_state)

            self._mute_state = mute_state
            if (mute_state):
                self._logger.debug("Mute set")
            else:
                self._logger.debug("Mute unset")

    async def _set_mute_state(self, mute_state):
        """
        Coroutine for setting the mute_state similarly to method
        `set_mute_state`.

        :param mute_state: State to set
        :type: bool
        """
        if (self._mute_state != mute_state):
            await asyncio.to_thread(self._mute_cb, mute_state)

            self._mute_state = mute_state
            if (mute_state):
                self._logger.debug("Mute set")
            else:
                self._logger.debug("Mute unset")

    def _start_monitor_mute(self):
        """
        Thread entry point to continuously process mute events concurrently
        apart from the main thread.
        Serves to spawn the `self._monitor_mute` coroutine, as required on
        account of the aynchronous API of the libpulse bindings.
        """
        asyncio.run(self._monitor_mute())

    async def _monitor_mute(self):
        """
        Coroutine for processing events from the sound system.
        Maintains an event loop for retrieving changes in the state of the
        Pulseaudio or Pipewire-pulse based sound server.
        """
        self._logger.debug("Started mute monitoring thread")
        async with LibPulse('openrazer-daemon') as lib_pulse:
            self._logger.debug("Connected to libpulse")
            # find the index of the current, default sink
            server_info = await lib_pulse.pa_context_get_server_info()
            self.def_sink_name = server_info.default_sink_name
            def_sink_info = await lib_pulse.pa_context_get_sink_info_by_name(
                    self.def_sink_name)
            self._logger.debug("Current default sink is `{0}` ({1})".format(def_sink_info.index, self.def_sink_name))

            await self._set_mute_state(def_sink_info.mute == 1)

            # wait for sink changes (mute state) and also server changes
            #  (default sink switches)
            await lib_pulse.pa_context_subscribe(PA_SUBSCRIPTION_MASK_SINK |
                                                 PA_SUBSCRIPTION_MASK_SERVER)
            iterator = lib_pulse.get_events()

            while (self._monitoring):
                try:
                    async for event in iterator:
                        if isinstance(event, LibPulseError):
                            raise event

                        await self.__process_event(lib_pulse, event)
                        if not self._monitoring:
                            break
                except TimeoutError:
                    pass
                # Iterator exceptions, these will never happen
                except LibPulseError as e:
                    raise e

            iterator.close()

    async def __process_event(self, lib_pulse, event):
        if (event.facility == 'server' and event.type == 'change'):
            server_info = await lib_pulse.pa_context_get_server_info()
            chg_def_sink_name = server_info.default_sink_name

            if (self.def_sink_name != chg_def_sink_name):
                self.def_sink_name = chg_def_sink_name
                def_sink_info = await lib_pulse.pa_context_get_sink_info_by_name(chg_def_sink_name)
                self._logger.debug("Current default sink is `{0}` ({1})".format(def_sink_info.index, chg_def_sink_name))
                await self._set_mute_state(def_sink_info.mute == 1)

        elif (event.facility == 'sink' and event.type == 'change'):
            def_sink_info = await lib_pulse.pa_context_get_sink_info_by_name(self.def_sink_name)
            await self._set_mute_state(def_sink_info.mute == 1)
