import time

from .SequencerConstants import RESOLUTION_MAP
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

        self._full_loop_start = 0.0
        self._full_loop_end = 0.0
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

        if clip is not None:
            self._full_loop_start = clip.loop_start
            self._full_loop_end = clip.loop_end

    @property
    def _mode(self):
        return self._step_sequencer._mode

    def set_note_cache(self, note_cache):
        self._note_cache = note_cache

    def set_playhead(self, playhead, updateBlock=False):
        self._playhead = playhead

        if not self.is_enabled():
            return

        self.update()

    @property
    def _is_mute_shifted(self):
        return self._step_sequencer._is_mute_shifted

    @property
    def _is_velocity_shifted(self):
        return self._step_sequencer._note_editor._is_velocity_shifted

    # @property
    # def _quantization(self):
    #     return self._step_sequencer._quantization

    @property
    def _resolution(self):
        return RESOLUTION_MAP[self._step_sequencer._resolution_index]

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

        if self._clip is None:
            return

        try:

            clip_max = max(
                self._clip.length,
                self._clip.loop_end,
                self._clip.end_marker,
                end
            )

            start = max(0.0, min(start, clip_max))
            end = max(start + self._resolution, min(end, clip_max))
            #end = max(start + self._quantization, min(end, clip_max))

            if start >= end:
                return

            self._loop_start = start
            self._loop_end = end

            # ----------------------------------------
            # IMPORTANT:
            # expand markers FIRST
            # ----------------------------------------

            if end > self._clip.end_marker:
                self._clip.end_marker = end

            if start < self._clip.start_marker:
                self._clip.start_marker = start

            # ----------------------------------------
            # expand loop bounds
            # ----------------------------------------

            if end > self._clip.loop_end:
                self._clip.loop_end = end

            if start < self._clip.loop_start:
                self._clip.loop_start = start

            # ----------------------------------------
            # final exact values
            # ----------------------------------------

            self._clip.loop_start = start
            self._clip.loop_end = end

            self._clip.start_marker = start
            self._clip.end_marker = end

            # self._debug(
            #     f"APPLIED LOOP start={start} end={end}"
            # )

        except (RuntimeError, IndexError) as e:

            self._debug(f"SET LOOP FAILED: {str(e)}")
            return

        self.update()

    def set_loop_page_offset(self, offset):
        self._loop_page_offset = offset

    def _loop_button_value(self, value, sender):
        # self._control_surface.log_message(
        #     "[LOOP BUTTON] enabled=%s value=%d"
        #     % (self.is_enabled(), value)
        # )
        if self.is_enabled():

            idx = self._buttons.index(sender)
            # self._debug(
            #     f"button={idx} value={value} "
            #     f"loop1={self._loop_point1} "
            #     f"loop2={self._loop_point2}"
            # )
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

                    if (
                            self._last_button_idx == idx and
                            (time.time() - self._last_button_time) < 0.25
                    ):
                        # debug
                        # self._debug("DOUBLE TAP DETECTED")
                        setloop = True
                        self._last_button_time = time.time()
                        self._last_button_idx = -1

                if self._loop_point1 != -1 and self._loop_point2 != -1:

                    start = min(self._loop_point1, self._loop_point2)
                    end = max(self._loop_point1, self._loop_point2) + 1

                    # relative block index (0-7)
                    self._block = start

                    if setloop:

                        # absolute block indexes
                        absolute_start = start + (self._loop_page_offset * 8)
                        absolute_end = end + (self._loop_page_offset * 8)
                        # self._debug(
                        #     f"selection start={absolute_start} "
                        #     f"end={absolute_end}"
                        # )
                        if absolute_end <= absolute_start:
                            return

                        new_start = (
                                absolute_start *
                                self._blocksize *
                                # self._quantization
                                self._resolution
                        )

                        new_end = (
                                absolute_end *
                                self._blocksize *
                                # self._quantization
                                self._resolution
                        )

                        # -----------------------------------------
                        # SAFETY FIX: DISABLED MUTE / DELETE MODES
                        # -----------------------------------------
                        # Previously checked: if self._is_mute_shifted:
                        # Since we removed the Shift/Mute buttons, we force NORMAL behavior always.
                        # This prevents accidental deletion of notes when pressing loop buttons.

                        # --- NORMAL LOOP SELECTION (ALWAYS EXECUTED) ---

                        # SAME LOOP SELECTED AGAIN
                        # -> restore full clip
                        if self._is_selecting_current_loop(absolute_start, absolute_end):

                            self._control_surface.log_message(
                                "[LoopSelector] RESTORING FULL LOOP "
                                "stored=(%s,%s)" % (
                                    self._full_loop_start,
                                    self._full_loop_end
                                )
                            )

                            self.set_clip_loop(
                                self._full_loop_start,
                                self._full_loop_end
                            )

                        else:

                            # NOTE: Removed 'if self._is_velocity_shifted' check here too.
                            # We no longer support extending clip content via velocity shift.
                            # Just set the loop normally.

                            # self._debug(
                            #     f"SETTING LOOP "
                            #     f"{new_start} -> {new_end}"
                            # )
                            self.set_clip_loop(
                                new_start,
                                new_end
                            )

                    # sequencer page follows selection
                    absolute_block = (
                            self._block +
                            (self._loop_page_offset * 8)
                    )

                    # Move the Sequencer Page to match the selection
                    self._step_sequencer.set_page(absolute_block)

                    self._loop_point1 = -1
                    self._loop_point2 = -1

                    self.update()

                self._last_button_time = time.time()
                self._last_button_idx = idx

    def _is_selecting_current_loop(self, start, end):

        block_size = (
                self._blocksize *
                # self._quantization
                self._resolution
        )

        current_start_block = int(
            round(self._clip.loop_start / block_size)
        )

        current_end_block = int(
            round(self._clip.loop_end / block_size)
        )

        same = (
                current_start_block == start and
                current_end_block == end
        )

        # self._debug(
        #     f"current=({current_start_block},{current_end_block}) "
        #     f"selected=({start},{end}) "
        #     f"same={same}"
        # )

        return same

    # Index check for page boundaries scroll OK
    def can_scroll(self, blocks):
        if self._clip != None:
            new_block = self._block + blocks
            if new_block < 0 or new_block >= 8:
                return False  # Cannot scroll beyond 0-7
            absolute_block = new_block + (self._loop_page_offset * 8)
            # if (absolute_block * self._blocksize * self._quantization < self._clip.loop_start) or (
            #         (absolute_block + 1) * self._blocksize * self._quantization > self._clip.loop_end):
            if (absolute_block * self._blocksize * self._resolution < self._clip.loop_start) or (
                (absolute_block + 1) * self._blocksize * self._resolution > self._clip.loop_end):
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
        # --- SAFE INITIALIZATION CHECKS ---
        step_seq = getattr(self, '_step_sequencer', None)

        if step_seq is None:
            return

        note_editor = getattr(step_seq, '_note_editor', None)
        is_animating = False

        if note_editor is not None:
            vel_anim = getattr(note_editor, '_velocity_wait_animation', False)
            len_anim = getattr(note_editor, '_length_wait_animation', False)
            is_animating = (vel_anim or len_anim)

        if self.is_enabled():
            self._get_clip_loop()

            clip_is_playing = (
                    self._clip is not None and
                    self._clip.is_playing
            )

            i = 0

            for button in self._buttons:
                # Determine if THIS button is Row 7 (the last one)
                # Assuming buttons are ordered 0-15, Row 7 corresponds to indices 8-15?
                # Wait, LoopSelector usually controls 8 buttons (Row 7 only).
                # Let's check your _set_loop_selector logic:
                # "self._matrix.get_button(x, 7)" -> This means the Loop Selector has 8 buttons (x=0..7), all on Row 7.

                # In your code: self._buttons is a list of 8 buttons (one for each column x, row y=7).
                # So 'i' here IS the column index (0-7) corresponding to Row 7 buttons.

                # CRITICAL FIX: If animating, SKIP drawing ANY Loop Selector buttons (all 8 are Row 7).
                if is_animating:
                    # Do not update these buttons; let the Note Editor handle them via _update_matrix
                    i += 1
                    continue

                if self._clip is None:
                    button.set_light("DefaultButton.Disabled")
                    if self._cache[i] != button._off_value:
                        button.turn_off()
                        self._cache[i] = button._off_value
                else:
                    absolute_block = (i + (self._loop_page_offset * 8))
                    block_start = (absolute_block * self._blocksize * self._resolution)
                    block_end = ((absolute_block + 1) * self._blocksize * self._resolution)

                    in_loop = (block_start < self._loop_end and block_start >= self._loop_start)
                    playing = (
                                self._playhead is not None and self._playhead >= block_start and self._playhead < block_end)
                    selected = (i == self._block)

                    if not clip_is_playing:
                        if in_loop:
                            self._cache[i] = "StepSequencer.LoopSelector.Stopped"
                        else:
                            self._cache[i] = "DefaultButton.Disabled"
                        if selected:
                            self._cache[i] = "StepSequencer.LoopSelector.StoppedSelected"
                    else:
                        if in_loop:
                            if playing:
                                if selected:
                                    self._cache[i] = "StepSequencer.LoopSelector.SelectedPlaying"
                                else:
                                    self._cache[i] = "StepSequencer.LoopSelector.Playing"
                            else:
                                if selected:
                                    self._cache[i] = "StepSequencer.LoopSelector.Selected"
                                else:
                                    self._cache[i] = "StepSequencer.LoopSelector.InLoop"
                        else:
                            if playing:
                                if selected:
                                    self._cache[i] = "StepSequencer.LoopSelector.SelectedPlaying"
                                else:
                                    self._cache[i] = "StepSequencer.LoopSelector.Playing"
                            else:
                                if selected:
                                    self._cache[i] = "StepSequencer.LoopSelector.Selected"
                                else:
                                    self._cache[i] = "DefaultButton.Disabled"

                    if (self._cache[i] != button._on_value or self._force):
                        button.set_light(self._cache[i])
                i += 1

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

    # debug
    def _debug(self, msg):
        self._control_surface.log_message("[LoopSelector] " + str(msg))