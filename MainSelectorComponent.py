# -*- coding: utf-8 -*-

from _Framework.ModeSelectorComponent import ModeSelectorComponent
from _Framework.ButtonElement import ButtonElement
from _Framework.ButtonMatrixElement import ButtonMatrixElement
from _Framework.SessionZoomingComponent import DeprecatedSessionZoomingComponent# noqa
from .DeviceControllerComponent import DeviceControllerComponent
from .SpecialSessionComponent import SpecialSessionComponent
from .InstrumentControllerComponent import InstrumentControllerComponent
from .SubSelectorComponent import SubSelectorComponent  # noqa
from .StepSequencerComponent import StepSequencerComponent
from .StepSequencerComponent2 import StepSequencerComponent2
from .NoteRepeatComponent import NoteRepeatComponent
from _Framework.SceneComponent import SceneComponent
from .SpecialProSessionComponent import SpecialProSessionComponent
import Live
import time
try:
    from .Settings import Settings
except ImportError:
    from .Settings import *

from .ModeConstants import MainMode


class MainSelectorComponent(ModeSelectorComponent):

	""" Class that reassigns the button on the launchpad to different functions """


	def __init__(self, matrix, top_buttons, side_buttons, config_button, osd, control_surface, note_repeat, c_instance):
		#verify matrix dimentions
		assert isinstance(matrix, ButtonMatrixElement)
		assert ((matrix.width() == 8) and (matrix.height() == 8))
		assert isinstance(top_buttons, tuple)
		assert (len(top_buttons) == 8)
		assert isinstance(side_buttons, tuple)
		assert (len(side_buttons) == 8)
		assert isinstance(config_button, ButtonElement)
		assert isinstance(note_repeat, NoteRepeatComponent)
		ModeSelectorComponent.__init__(self) #super constructor
		
		#inject ControlSurface and OSD components (M4L)
		self._matrix = matrix
		self._nav_buttons = top_buttons[:4]#arrow buttons
		#self._mode_buttons = top_buttons[4:]#session,h-arpeggiator,custom drum mode,Melodic sequencer
		self._mode_buttons = (
			top_buttons[4],  # Session (unchanged)
			top_buttons[5],  # h-arpeggiator
			top_buttons[6],  # custom drum mode
			top_buttons[7],  # Melodic sequencer
		)
		self._side_buttons = side_buttons#launch buttons
		self._config_button = config_button#used to reset launchpad
		self._osd = osd
		self._control_surface = control_surface
		self._note_repeat = note_repeat
		self._c_instance = c_instance
		self._pro_session_on = False
		self._long_press = 500
		self._last_session_mode_button_press = int(round(time.time() * 1000))
		self._aux_scene = None
		#Non-Matrix buttons
		self._all_buttons = []
		for button in self._side_buttons + self._nav_buttons:
			self._all_buttons.append(button)

		# initialize modes
		self._stepseq = None
		self._stepseq2 = None
		self._instrument_controller = None
		self._device_controller = None
		self._session = None

		#initialize index variables
		self._main_mode_index = MainMode.SESSION
		#self._mode_index = 0 #Inherited from parent
		#sself._main_mode_index = 0 #LP original modes

		#self._sub_mode_list = [0, 0, 0, 0]
		#for index in range(4):
		#	self._sub_mode_list[index] = 0
		self.set_mode_buttons(self._mode_buttons)
		self._last_mode_index = 0 
			

		self._clip_stop_buttons = [] 
		for column in range(8):
			self._clip_stop_buttons.append(matrix.get_button(column,matrix.height()-1))
		self._session = SpecialProSessionComponent(matrix.width(), matrix.height(), None, self._side_buttons, self._control_surface, self, self._c_instance.song())

		#initialize _session variables	
		self._session.set_osd(self._osd)
		self._session.name = 'Session_Control'



		###ZOOMING COMPONENT
		self._zooming = DeprecatedSessionZoomingComponent(self._session, enable_skinning = True)
		self._zooming.name = 'Session_Overview'
		self._zooming.set_empty_value("DefaultButton.Disabled")
		
		#Non-Matrix buttons
		self._all_buttons = []
		for button in self._side_buttons + self._nav_buttons:
			self._all_buttons.append(button)

		#SubSelector changes the Mode using side buttons -> MIXER MODE (ie. Pan, Volume, Send1, Send2, Stop, Solo, Activate, Arm)
		self._sub_modes = SubSelectorComponent(matrix, side_buttons, self._session, self._control_surface)
		self._sub_modes.name = 'Mixer_Modes'
		self._sub_modes._mixer.set_osd(self._osd)
		self._sub_modes.set_update_callback(self._update_control_channels)

		#User2 stepSequencer (Drum & Melodic)
		self._stepseq = StepSequencerComponent(self._matrix, self._side_buttons, self._nav_buttons, self._control_surface)
		self._stepseq.set_osd(self._osd)
		
		#User2 stepSequencer (Retro style)
		self._stepseq2 = StepSequencerComponent2(self._matrix, self._side_buttons, self._nav_buttons, self._control_surface)
		self._stepseq2.set_osd(self._osd)
		# debug
		# for button, (x, y) in self._matrix.iterbuttons():
		# 	self.log_message(
		# 		f"button {x},{y} listeners={button.value_has_listener(self._stepseq2._matrix_value)}"
		# 	)
		
		#User1 Instrument controller (Scale)
		self._instrument_controller = InstrumentControllerComponent(self._matrix, self._side_buttons, self._nav_buttons, self._control_surface, self._note_repeat)
		self._instrument_controller.set_osd(self._osd)
		#self._instrument_controller = None
		
		#User1 Device controller (Fx or Instrument parameters)
		self._device_controller = DeviceControllerComponent(control_surface = self._control_surface, matrix = self._matrix, side_buttons = self._side_buttons, top_buttons =  self._nav_buttons)
		self._device_controller.set_osd(self._osd)

		self._init_session()
		self._all_buttons = tuple(self._all_buttons)

		self._apply_main_mode()

	def disconnect(self):
		for button in self._modes_buttons:
			button.remove_value_listener(self._mode_value)

		self._session = None
		self._zooming = None
		for button in self._all_buttons:
			button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")

		self._config_button.turn_off()
		self._matrix = None
		self._side_buttons = None
		self._nav_buttons = None
		self._config_button = None
		ModeSelectorComponent.disconnect(self)

	def _apply_main_mode(self):
		# Stop all components first
		self._setup_session(False, False)
		self._setup_instrument_controller(False)
		self._setup_device_controller(False)
		self._setup_mixer(False)

		if getattr(self, "_stepseq", None) is not None:
			self._stepseq.set_enabled(False)
		if getattr(self, "_stepseq2", None) is not None:
			self._stepseq2.set_enabled(False)

		# Apply selected mode
		if self._main_mode_index == 0:
			self._control_surface.show_message("SESSION MODE")
			self._setup_session(True, True)

		elif self._main_mode_index == 1:
			self._control_surface.show_message("INSTRUMENT MODE")
			self._setup_instrument_controller(True)

		elif self._main_mode_index == 2:
			self._control_surface.show_message("DRUM STEPSEQ MODE")
			self._setup_step_sequencer(True)

		elif self._main_mode_index == 3:
			self._control_surface.show_message("MELODIC STEPSEQ MODE")
			self._setup_step_sequencer2(True)

	def session_component(self):
		return self._session


	def set_mode(self, mode):
		self._main_mode_index = mode
		self._apply_main_mode()

	def _mode_value(self, value, sender):
		if sender is None:
			return

		if sender not in self._modes_buttons:
			return

		new_mode = self._modes_buttons.index(sender)

		# Add long press timer variable if not exists
		if not hasattr(self, '_last_stepseq2_mode_button_press'):
			self._last_stepseq2_mode_button_press = 0

		# -----------------------
		# BUTTON PRESS
		# -----------------------
		if value > 0:

			self._main_mode_index = new_mode

			# LONG PRESS LOGIC FOR SESSION MODE (existing)
			if new_mode == 0:
				now = int(round(time.time() * 1000))

				if self._last_mode_index == 0:
					self._last_session_mode_button_press = now
				else:
					if now - self._last_session_mode_button_press < self._long_press:
						self._pro_session_on = not self._pro_session_on

			# NEW: LONG PRESS LOGIC FOR MELODIC STEP SEQUIENCER (MODE 3)
			elif new_mode == 3:
				now = int(round(time.time() * 1000))

				# Check if we're already in this mode (double press scenario)
				if self._last_mode_index == 3:
					time_delta = now - self._last_stepseq2_mode_button_press

					# LONG PRESS DETECTED (>500ms)
					if time_delta >= self._long_press:
						# Trigger metadata scan
						if hasattr(self, '_stepseq2') and self._stepseq2:
							if hasattr(self._stepseq2, '_note_editor') and self._stepseq2._note_editor:
								if hasattr(self._stepseq2._note_editor, 'manual_scan_all_clips'):

									# Show feedback message
									self._control_surface.show_message("Scanning clips...")

									# Schedule scan to avoid blocking UI
									if hasattr(self._control_surface, 'schedule_message'):
										self._control_surface.schedule_message(
											1,
											self._do_metadata_scan_callback
										)
									else:
										# Fallback if schedule_message not available
										self._do_metadata_scan_callback()

					# Reset timer to prevent repeated triggers
					self._last_stepseq2_mode_button_press = 0
				else:
					# First press - record timestamp for next press detection
					self._last_stepseq2_mode_button_press = now

			self._last_mode_index = new_mode
			self._apply_main_mode()

		# -----------------------
		# BUTTON RELEASE
		# -----------------------
		else:
			# ONLY bookkeeping — no framework call
			self._last_mode_index = new_mode
			self.update()


	def number_of_modes(self):
		return 4 #1 + 3 + 3 + 1

	def on_enabled_changed(self):
		self.update()

	def _update_mode_buttons(self):
		self._modes_buttons[0].set_on_off_values("Mode.Session.On", "Mode.Session.Off")
		self._modes_buttons[1].set_on_off_values("Mode.Note.On", "Mode.Note.Off")  # instrument
		self._modes_buttons[2].set_on_off_values("Mode.StepSequencer.On", "Mode.StepSequencer.Off")  # drum
		self._modes_buttons[3].set_on_off_values("Mode.StepSequencer2.On", "Mode.StepSequencer2.Off")  # melodic

		for i in range(4):
			if i == self._main_mode_index:
				self._modes_buttons[i].turn_on()
			else:
				self._modes_buttons[i].turn_off()
		
	def getSkinName(self, user2Mode):
		if user2Mode=="instrument":
			user2Mode = "Note"
		if user2Mode=="device":
			user2Mode = "Device"
		if user2Mode=="user 1":
			user2Mode = "User"
		if user2Mode=="user 2":
			user2Mode = "User2"
		if user2Mode=="drum stepseq":
			user2Mode = "StepSequencer"
		if user2Mode=="melodic stepseq":
			user2Mode = "StepSequencer2"
		return user2Mode
		
	def channel_for_current_mode(self):
		# in this code, midi channels start at 0.
		# so channels range from 0 - 15.
		# mapping to 1-16 in the real world
		if self._main_mode_index == 0:
			return 0
		elif self._main_mode_index == 1:
			return 11
		elif self._main_mode_index == 2:
			return 1
		elif self._main_mode_index == 3:
			return 2
		return 0  # fallback safety


	def update(self):
		assert (self._modes_buttons != None)

		if not self.is_enabled():
			return

		self._update_mode_buttons()

		self._session.set_allow_update(False)
		self._zooming.set_allow_update(False)

		self._config_button.send_value(40)
		self._config_button.send_value(1)

		# ONLY ONE SOURCE OF TRUTH NOW
		self._apply_main_mode()

		self._session.set_allow_update(True)
		self._zooming.set_allow_update(True)



	def _setup_session(self, as_active, as_navigation_enabled):
		if getattr(self, "_session", None) is None:
			return
		assert isinstance(as_active, type(False))#assert is boolean
		for button in self._nav_buttons:
			if as_navigation_enabled:
				button.set_on_off_values("Mode.Session.On", "Mode.Session.Off")
			else:
				button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")

		# matrix
		self._activate_matrix(True)
		self._turn_off_scene_buttons()
		
		if (self._session.height() != self._matrix .height()) and (self._aux_scene != None):
			self._session._scenes.append(self._aux_scene)
		
		if as_active:
			self._session._set_pro_mode_on(self._pro_session_on)
		else:
			self._session._set_pro_mode_on(False)	
			
		for scene_index in range(self._session._num_scenes):#iterate over scenes
			scene = self._session.scene(scene_index)
			if as_active:#set scene launch buttons
				scene_button = self._side_buttons[scene_index]
				scene_button.set_enabled(as_active)
				if not self._pro_session_on:
					scene.set_launch_button(scene_button)
				else:
					scene.set_launch_button(None)
			else:
				scene.set_launch_button(None)  
				
			for track_index in range(self._session._num_tracks):#iterate over tracks of a scene -> clip slots
				if as_active:#set clip slot launch button
					button = self._matrix.get_button(track_index, scene_index)
					button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
					button.set_enabled(as_active)
					clip_slot = scene.clip_slot(track_index)
					if not self._pro_session_on:
						clip_slot.set_triggered_to_play_value("Session.ClipTriggeredPlay")
						clip_slot.set_stopped_value("Session.ClipStopped")
						clip_slot.set_started_value("Session.ClipStarted")
						clip_slot.set_launch_button(button)
					else:
						if(scene_index<self._matrix.height() -1):
							clip_slot.set_triggered_to_play_value("ProSession.ClipTriggeredPlay")
							clip_slot.set_stopped_value("ProSession.ClipStopped")
							clip_slot.set_started_value("ProSession.ClipStarted")
							clip_slot.set_launch_button(button)
						else:
							scene.clip_slot(track_index).set_launch_button(None)
				else:
					scene.clip_slot(track_index).set_launch_button(None)
					
		if (self._pro_session_on):
			self._aux_scene = self._session._scenes.pop(-1)
							
		if as_active:#Set up stop clip buttons and stop all clips button
			if self._pro_session_on:
				if self._clip_stop_buttons != None:
					for button in self._clip_stop_buttons:
						button.set_enabled(as_active)
				#	button.set_on_off_values("Session.StopClip", "DefaultButton.Disabled")						
						self._session.set_stop_track_clip_buttons(self._clip_stop_buttons)
				else:
					self._session.set_stop_track_clip_buttons(None)
			else:
				self._session.set_stop_track_clip_buttons(None)
		else:
			self._session.set_stop_track_clip_buttons(None)			
				
		if as_active:# zoom
			self._zooming.set_zoom_button(self._modes_buttons[0])# Set Session button as zoom shift button 
			self._zooming.set_button_matrix(self._matrix)
			self._zooming.set_scene_bank_buttons(self._side_buttons)
			self._zooming.set_nav_buttons(self._nav_buttons[0], self._nav_buttons[1], self._nav_buttons[2], self._nav_buttons[3])
			self._zooming.update()
		else:
			self._zooming.set_zoom_button(None)
			self._zooming.set_button_matrix(None)
			self._zooming.set_scene_bank_buttons(None)
			self._zooming.set_nav_buttons(None, None, None, None)

		if as_navigation_enabled: # nav buttons (track/scene)
			self._session.set_track_bank_buttons(self._nav_buttons[3], self._nav_buttons[2])
			self._session.set_scene_bank_buttons(self._nav_buttons[1], self._nav_buttons[0])
		else:
			self._session.set_track_bank_buttons(None, None)
			self._session.set_scene_bank_buttons(None, None)

		self._session.set_enabled(as_active)
		self._session._do_show_highlight()
		
		
	def _setup_instrument_controller(self, as_active):
		if getattr(self, "_instrument_controller", None) is not None:
			if as_active:
				self._activate_matrix(False) #Disable matrix buttons (clip slots)
				self._activate_scene_buttons(True)#Enable side buttons
				self._activate_navigation_buttons(True)#Enable nav buttons
			else:
				for scene_index in range(8):#Restore all matrix buttons and scene launch buttons
					scene_button = self._side_buttons[scene_index]
					scene_button.use_default_message() # Reset to original channel
					scene_button.force_next_send() #Flush
					for track_index in range(8):
						button = self._matrix.get_button(track_index, scene_index)
						button.use_default_message()# Reset to original channel
						button.force_next_send()#Flush
			self._instrument_controller.set_enabled(as_active)#Enable/disable instrument controller

	def _setup_device_controller(self, as_active):
		if getattr(self, "_device_controller", None) is not None:
			if as_active:
				self._activate_scene_buttons(True)
				self._activate_matrix(True)
				self._activate_navigation_buttons(True)
				self._device_controller._is_active = True
				self._config_button.send_value(32)
				self._device_controller.set_enabled(True)
				self._device_controller.update()
			else:
				self._device_controller._is_active = False
				temp=self._device_controller.set_enabled(False)

	def _setup_user_mode(self, release_matrix=True, release_side_buttons=True, release_nav_buttons=True, drum_rack_mode=True):
		# user1 -> All True but release_nav_buttons / user2 -> All false 
		for scene_index in range(8):
			scene_button = self._side_buttons[scene_index]
			scene_button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
			scene_button.force_next_send()
			scene_button.turn_off()
			scene_button.set_enabled((not release_side_buttons))#User2 enabled

			for track_index in range(8):
				button = self._matrix.get_button(track_index, scene_index)
				button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
				button.turn_off()
				button.set_enabled((not release_matrix))#User2 enabled

		for button in self._nav_buttons:
			button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
			button.turn_off()
			button.set_enabled((not release_nav_buttons)) #User1 & User2 enabled

		if drum_rack_mode:#User1 enabled
			self._config_button.send_value(2)#Set LP drum rack layout grid mapping mode
		self._config_button.send_value(32)#Send enable flashing led config message to LP
				
	def _setup_step_sequencer(self, as_active):
		if(self._stepseq != None):
			#if(self._stepseq.is_enabled() != as_active):
			if as_active:
				self._activate_scene_buttons(True)
				self._activate_matrix(True)
				self._activate_navigation_buttons(True)
				self._config_button.send_value(32)
				self._stepseq.set_enabled(True)
			else:
				self._stepseq.set_enabled(False)

	def _setup_step_sequencer2(self, as_active):
		if(self._stepseq2 != None):
			#sif(self._stepseq2.is_enabled() != as_active):
			if as_active:
				self._activate_scene_buttons(True)
				self._activate_matrix(True)
				self._activate_navigation_buttons(True)
				self._config_button.send_value(32)
				self._stepseq2.set_enabled(True)
				self._stepseq2.update()
			else:
				self._stepseq2.set_enabled(False)

	def _trigger_manual_metadata_scan(self):
		"""
		Called from long-press on StepSequencer2 button.
		Triggers full metadata cache rebuild from clip names.
		"""
		# Navigate to the actual method call chain:
		# MainSelectorComponent -> StepSequencerComponent2 -> MelodicNoteEditorComponent -> manual_scan_all_clips()

		if hasattr(self, '_stepseq2') and self._stepseq2:
			if hasattr(self._stepseq2, '_note_editor') and self._stepseq2._note_editor:
				# Call the manual scan method
				if hasattr(self._stepseq2._note_editor, 'manual_scan_all_clips'):

					# Schedule it to avoid blocking the button press handler
					if hasattr(self._control_surface, 'schedule_message'):
						self._control_surface.schedule_message(
							1,
							self._do_metadata_scan_callback
						)
					else:
						# Fallback if schedule_message not available
						self._do_metadata_scan_callback()
				else:
					self._control_surface.log_message("[SCAN_ERROR] manual_scan_all_clips not found")
			else:
				self._control_surface.log_message("[SCAN_ERROR] _note_editor not available")
		else:
			self._control_surface.log_message("[SCAN_ERROR] _stepseq2 not available")


	def _do_metadata_scan_callback(self):
		"""
		Callback executed after button press handler completes.
		Performs the actual metadata scan without blocking the UI.
		"""
		try:
			if not hasattr(self, '_stepseq2') or not self._stepseq2:
				self._control_surface.log_message("[SCAN_ERROR] _stepseq2 not available")
				return

			if not hasattr(self._stepseq2, '_note_editor') or not self._stepseq2._note_editor:
				self._control_surface.log_message("[SCAN_ERROR] _note_editor not available")
				return

			if not hasattr(self._stepseq2._note_editor, 'manual_scan_all_clips'):
				self._control_surface.log_message("[SCAN_ERROR] manual_scan_all_clips method missing")
				return

			# Execute the scan
			total, found = self._stepseq2._note_editor.manual_scan_all_clips()

			# Display results
			msg = f"Scanned {total}, Found {found}"
			self._control_surface.show_message(msg)
			self._control_surface.log_message(f"[METADATA_SCAN] {msg}")

		except Exception as e:
			self._control_surface.log_message(f"[SCAN_ERROR] {e}")
			import traceback
			self._control_surface.log_message(traceback.format_exc())
			self._control_surface.show_message("Scan failed")


	def _setup_mixer(self, as_active):

		# object not created yet during init
		if not hasattr(self, "_sub_modes") or self._sub_modes is None:
			return

		assert isinstance(as_active, type(False))

		if as_active:
			self._activate_navigation_buttons(True)
			self._activate_scene_buttons(True)
			self._activate_matrix(True)

			self._sub_modes.set_enabled(True)

			if self._sub_modes.is_enabled():
				self._sub_modes.set_mode(-1)
			else:
				self._sub_modes.release_controls()

		else:
			self._sub_modes.set_enabled(False)
			self._sub_modes.release_controls()

	def _init_session(self):
		#self._session.set_stop_clip_value("Session.StopClip")
		#self._session.set_stop_clip_triggered_value("Session.ClipTriggeredStop")
		
		session_height = self._matrix.height()
		#if self._session._stop_clip_buttons != None:
		#	session_height = self._matrix.height()-1
	
		for scene_index in range(session_height):
			for track_index in range(self._matrix.width()):
				self._all_buttons.append(self._matrix.get_button(track_index, scene_index))


	def _activate_navigation_buttons(self, active):
		for button in self._nav_buttons:
			button.set_enabled(active)

	def _activate_scene_buttons(self, active):
		for button in self._side_buttons:
			button.set_enabled(active)

	def _activate_matrix(self, active):
		for scene_index in range(8):
			for track_index in range(8):
				self._matrix.get_button(track_index, scene_index).set_enabled(active)

	def _turn_off_scene_buttons(self):
		for side_button in self._side_buttons:
			side_button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
			side_button.turn_off()

	def log_message(self, msg):
		self._control_surface.log_message(msg)

	# Update the channels of the buttons in the user modes..
	def _update_control_channels(self):
		new_channel = self.channel_for_current_mode()
		for button in self._all_buttons:
			button.set_channel(new_channel)
			button.force_next_send()
