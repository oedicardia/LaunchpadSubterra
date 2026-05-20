import time

from _Framework.ButtonElement import ButtonElement
from _Framework.ControlSurfaceComponent import ControlSurfaceComponent

STEPSEQ_MODE_MULTINOTE = 2
class LoopSelectorComponent(ControlSurfaceComponent):

    def __init__(self, step_sequencer, buttons, control_surface):
        ControlSurfaceComponent.__init__(self)
        self._control_surface = control_surface
        self.set_enabled(False)
        self._step_sequencer = step_sequencer

        self._clip = None  # clip being played
        self._notes = None  # notes of the clip
        self._playhead = None  # contains the clip playing position

        self._loop_end = 0
        self._loop_start = 0

        self._blocksize = 8  # number of notes per block -> how many steps are in a button (depending on quantization for note length variable)
        self._block = 0  # currently selected block (button)
        self._force = True  # used to force a state change / message send

        # used for loop selection
        self._last_button_idx = -1
        self._last_button_time = time.time()
        self._loop_point1 = -1
        self._loop_point2 = -1

        self._cache = [-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
                       -1, -1]  # Length=16

        self._buttons = buttons
        for button in self._buttons:  # iterate 16 buttons of 4x4 lower right matrix section
            assert isinstance(button, ButtonElement)
            button.remove_value_listener(self._loop_button_value)
            button.add_value_listener(self._loop_button_value,
                                      identify_sender=True)

    def disconnect(self):
        self._top_buttons = None

    @property
    def _number_of_lines_per_note(self):
        if self._mode == STEPSEQ_MODE_MULTINOTE:
            return self._step_sequencer._number_of_lines_per_note
        else:
            return 1

    def set_clip(self, clip):
        self._clip = clip

    @property
    def _mode(self):
        return self._step_sequencer._mode

    def set_note_cache(self, note_cache):
        self._note_cache = note_cache

    def set_playhead(self, playhead, updateBlock=False):
        self._playhead = playhead
        # NEVER modify self._block from playhead updates
        # self._block represents USER selection only
        self.update()

    @property
    def _is_mute_shifted(self):
        return self._step_sequencer._is_mute_shifted

    @property
    def _is_velocity_shifted(self):
        return self._step_sequencer._note_editor._is_velocity_shifted

    @property
    def _quantization(self):
        return self._step_sequencer._quantization

    @property
    def block(self):
        return self._block

    def set_blocksize(self, blocksize):
        self._blocksize = blocksize

    def set_enabled(self, enabled):
        self._force = True
        ControlSurfaceComponent.set_enabled(self, enabled)

    # Read Live's Clip loop values to LoopSelector Values OK
    def _get_clip_loop(self):
        if self._clip != None:
            self._loop_start = self._clip.loop_start
            self._loop_end = self._clip.loop_end
        else:
            self._loop_start = 0
            self._loop_end = 0

    # Write LoopSelector Values to Live's Clip loop values (loop and marker) OK
    def set_clip_loop(self, start, end):
        if self._clip != None:

            # --- SANITY CHECKS ---
            clip_length = max(
                self._clip.loop_end,
                self._clip.end_marker,
                end
            )

            start = max(0, start)
            end = max(start + self._quantization, end)
            # ---------------------

            self._loop_start = start
            self._loop_end = end

            try:
                if self._loop_start >= self._clip.loop_end:
                    self._clip.loop_end = self._loop_end
                    self._clip.loop_start = self._loop_start
                    self._clip.end_marker = self._loop_end
                    self._clip.start_marker = self._loop_start
                else:
                    self._clip.loop_start = self._loop_start
                    self._clip.loop_end = self._loop_end
                    self._clip.start_marker = self._loop_start
                    self._clip.end_marker = self._loop_end

            except RuntimeError:
                return

            self.update()
    # def set_clip_loop(self, start, end):
    #     if self._clip != None:
    #         self._loop_end = end
    #         self._loop_start = start
    #         if self._loop_start >= self._clip.loop_end:
    #             self._clip.loop_end = self._loop_end
    #             self._clip.loop_start = self._loop_start
    #             self._clip.end_marker = self._loop_end
    #             self._clip.start_marker = self._loop_start
    #         else:
    #             self._clip.loop_start = self._loop_start
    #             self._clip.loop_end = self._loop_end
    #             self._clip.start_marker = self._loop_start
    #             self._clip.end_marker = self._loop_end
    #         self.update()

    def set_loop_page_offset(self, offset):
        self._loop_page_offset = offset

    # LoopSelector listener OK
    def _loop_button_value(self, value, sender):
        if self.is_enabled():
            idx = self._buttons.index(sender)
            if value > 0:
                pressed = 0
                for b in self._buttons:
                    if b.is_pressed():
                        pressed += 1

                if pressed > 2:
                    return
                if self._loop_point1 == -1:
                    self._loop_point1 = idx
                elif self._loop_point2 == -1:
                    self._loop_point2 = idx

            elif self._loop_point1 != -1:
                setloop = self._loop_point2 != -1
                if self._loop_point2 == -1:
                    self._loop_point2 = idx
                    if self._last_button_idx == idx and (time.time() - self._last_button_time) < 0.25:
                        setloop = True
                        self._last_button_time = time.time()
                        self._last_button_idx = -1

                if self._loop_point1 != -1 and self._loop_point2 != -1:
                    start = min(self._loop_point1, self._loop_point2)
                    end = max(self._loop_point1, self._loop_point2) + 1

                    # _block is the relative index (0-7)
                    self._block = start  # This is correct: start is 0-7

                    if setloop:
                        # Use absolute block for loop operations
                        absolute_start = start + (self._loop_page_offset * 8)
                        absolute_end = end + (self._loop_page_offset * 8)
                        if absolute_end <= absolute_start:
                            return
                        if self._is_mute_shifted:
                            if self._is_velocity_shifted:
                                self._mute_notes_in_range(
                                    absolute_start * self._blocksize * self._quantization,
                                    absolute_end * self._blocksize * self._quantization)
                            else:
                                self._delete_notes_in_range(
                                    absolute_start * self._blocksize * self._quantization,
                                    absolute_end * self._blocksize * self._quantization)
                        else:
                            if self._is_velocity_shifted:
                                self._extend_clip_content(
                                    absolute_start * self._blocksize * self._quantization,
                                    self._loop_end,
                                    absolute_end * self._blocksize * self._quantization)
                            self.set_clip_loop(
                                absolute_start * self._blocksize * self._quantization,
                                absolute_end * self._blocksize * self._quantization)

                    # Calculate the absolute block for set_page
                    absolute_block = self._block + (self._loop_page_offset * 8)
                    # set sequencer focus
                    self._step_sequencer.set_page(absolute_block)
                    self._loop_point1 = -1
                    self._loop_point2 = -1
                    self.update()
                self._last_button_time = time.time()
                self._last_button_idx = idx

    # Index check for page boundaries scroll OK
    def can_scroll(self, blocks):
        if self._clip != None:
            new_block = self._block + blocks
            if new_block < 0 or new_block >= 8:
                return False  # Cannot scroll beyond 0-7
            absolute_block = new_block + (self._loop_page_offset * 8)
            if (absolute_block * self._blocksize * self._quantization < self._clip.loop_start) or (
                    (absolute_block + 1) * self._blocksize * self._quantization > self._clip.loop_end):
                return False
            return True
        return False

    # Does the actual scroll OK
    def scroll(self, blocks):
        if self._clip != None and self.can_scroll(blocks):
            # Modify _block relatively (e.g., -1 or +1)
            new_block = self._block + blocks
            # Ensure new_block is within 0-7
            if 0 <= new_block < 8:
                self._block = new_block
                absolute_block = self._block + (self._loop_page_offset * 8)
                self._step_sequencer.set_page(absolute_block)

    # Iterates refreshing all loop selector buttons (called from playing position listener) OK
    def update(self):
        if self.is_enabled():
            self._get_clip_loop()
            i = 0
            for button in self._buttons:
                if self._clip == None:
                    button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
                    if self._cache[i] != button._off_value:
                        button.turn_off()
                        self._cache[i] = button._off_value
                else:
                    # Calculate the absolute block index for this button
                    absolute_block = i + (self._loop_page_offset * 8)
                    in_loop = (
                                      absolute_block * self._blocksize * self._quantization < self._loop_end) and (
                                      absolute_block * self._blocksize * self._quantization >= self._loop_start)
                    playing = self._playhead != None and self._playhead >= absolute_block * self._blocksize * self._quantization and self._playhead < (
                            absolute_block + 1) * self._blocksize * self._quantization
                    # _block is the relative index (0-7) within the current page
                    selected = i == self._block
                    if in_loop:
                        if playing:
                            if selected:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.SelectedPlaying"
                            else:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.Playing"
                        else:
                            if selected:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.Selected"
                            else:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.InLoop"
                    else:
                        if playing:
                            if selected:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.SelectedPlaying"
                            else:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.Playing"
                        else:
                            if selected:
                                self._cache[
                                    i] = "StepSequencer.LoopSelector.Selected"
                            else:
                                self._cache[i] = "DefaultButton.Disabled"

                    if self._cache[
                        i] != button._on_value or self._force:  # Enable/turn on all buttons
                        button.set_on_off_values(self._cache[i], self._cache[i])
                        button.turn_on()
                i = i + 1
            self._force = False

    # Make a copy of the current loop to the next N empty blocks OK
    def _extend_clip_content(self, loop_start, old_loop_end, new_loop_end):
        if (self._no_notes_in_range(old_loop_end, new_loop_end, True)):
            clip_looping_length = 0
            if (old_loop_end > 1):
                power = 1
                while (power * 2 < old_loop_end):
                    power *= 2
                clip_looping_length = (power)
            clone_length = new_loop_end - old_loop_end
            if (clip_looping_length > 0):
                clone_start_point = (old_loop_end % clip_looping_length)
            else:
                clone_start_point = 0
            self._copy_notes_in_range(clone_start_point,
                                      clone_start_point + clone_length,
                                      old_loop_end)

    # Does the note by note copy OK
    def _copy_notes_in_range(self, start, end, new_start):
        new_notes = list(self._note_cache)
        # for i in range()
        for note in new_notes:
            if note[1] >= start and note[1] < end:
                new_notes.append(
                    [note[0], note[1] + new_start - start, note[2], note[3],
                     note[4]])
        self._clip.select_all_notes()
        self._clip.replace_selected_notes(tuple(new_notes))

    # Checks if a range is empty OK
    def _no_notes_in_range(self, start, end, or_after):
        for note in list(self._note_cache):
            if note[1] >= start and (note[1] < end or or_after):
                return (False)
        return (True)

    # Deletes a block of notes OK
    def _delete_notes_in_range(self, start, end):
        new_notes = list()
        for note in list(self._note_cache):
            if note[1] < start or note[1] >= end:
                new_notes.append(note)
        self._clip.select_all_notes()
        self._clip.replace_selected_notes(tuple(new_notes))

    # Mutes a block of notes OK
    def _mute_notes_in_range(self, start, end):
        new_notes = list()
        for note in list(
            self._note_cache):  # Note -> tuple containing pitch, time, duration, velocity, and mute
            if note[1] < start or note[1] >= end:  # Note time
                new_notes.append(note)
            else:
                new_notes.append([note[0], note[1], note[2], note[3],
                                  not note[4]])  # Negate mute state
        self._clip.select_all_notes()
        self._clip.replace_selected_notes(tuple(new_notes))
