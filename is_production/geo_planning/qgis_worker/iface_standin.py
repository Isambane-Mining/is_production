"""A stand-in for QGIS Desktop's ``iface``, so desktop plugins that register Processing algorithms from
``initGui()`` can be started in the headless processing worker, unchanged.

Many QGIS plugins add their Processing provider in ``initGui()`` (next to their toolbar buttons and menu
entries) instead of ``initProcessing()``. Starting such a plugin needs an ``iface``: this module provides one.

- What plugins commonly use is real, just never shown (offscreen Qt platform): a main window, a map canvas on
  the current project, a message bar, menus and toolbars. ``addToolBarIcon``, ``addPluginToMenu`` and the like
  accept the plugin's actions and record them.
- Anything else a plugin asks ``iface`` for returns a *null object* that accepts any call, attribute, signal
  connection or iteration and does nothing, so desktop-only hooks (dock widgets, layer tree views, custom map
  tools, …) do not stop the plugin from starting.

Only the plugin's Processing algorithms are used; its dialogs are never opened here.

This file imports QGIS (GPL-2.0-or-later); see the licence note in the app README.
"""


class Null:
	"""Accepts any use and does nothing: calls, attributes, signal .connect(), iteration, comparisons."""

	def __call__(self, *args, **kwargs):
		return self

	def __getattr__(self, name):
		return self

	def __iter__(self):
		return iter(())

	def __bool__(self):
		return False

	def __len__(self):
		return 0

	def __repr__(self):
		return "<iface stand-in: not available headless>"


NULL = Null()


def make_iface():
	from qgis.core import QgsProject
	from qgis.gui import QgsMapCanvas, QgsMessageBar
	from qgis.PyQt.QtCore import QObject, QSize
	from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QToolBar

	class StandInInterface(QObject):
		def __init__(self):
			super().__init__()
			self._main = QMainWindow()
			self._canvas = QgsMapCanvas(self._main)
			self._canvas.setDestinationCrs(QgsProject.instance().crs())
			self._bar = QgsMessageBar(self._main)
			self._menus = {}
			self._toolbars = {}
			self.actions = []  # (where, text) of every action a plugin added
			self.messages = []

		# --- the main window and its parts
		def mainWindow(self):
			return self._main

		def mapCanvas(self):
			return self._canvas

		def messageBar(self):
			return self._bar

		def iconSize(self, dockedToolbar=False):
			return QSize(24, 24)

		def activeLayer(self):
			return None

		def setActiveLayer(self, layer):
			return False

		def _menu(self, name):
			if name not in self._menus:
				self._menus[name] = QMenu(name, self._main)
			return self._menus[name]

		def _toolbar(self, name):
			if name not in self._toolbars:
				self._toolbars[name] = QToolBar(name, self._main)
			return self._toolbars[name]

		def _record(self, where, action):
			try:
				self.actions.append((where, action.text()))
			except Exception:
				self.actions.append((where, str(action)))
			return action

		# --- toolbars
		def addToolBar(self, name, *args):
			return self._toolbar(name if isinstance(name, str) else "Toolbar")

		def addToolBarIcon(self, action):
			self._record("toolbar", action)
			return 0

		def removeToolBarIcon(self, action):
			pass

		def pluginToolBar(self):
			return self._toolbar("Plugins")

		def addVectorToolBarIcon(self, action):
			return self._record("vector toolbar", action) and 0

		def addRasterToolBarIcon(self, action):
			return self._record("raster toolbar", action) and 0

		def addDatabaseToolBarIcon(self, action):
			return self._record("database toolbar", action) and 0

		def addWebToolBarIcon(self, action):
			return self._record("web toolbar", action) and 0

		# --- menus
		def pluginMenu(self):
			return self._menu("Plugins")

		def vectorMenu(self):
			return self._menu("Vector")

		def rasterMenu(self):
			return self._menu("Raster")

		def databaseMenu(self):
			return self._menu("Database")

		def webMenu(self):
			return self._menu("Web")

		def addPluginToMenu(self, name, action):
			self._record(f"Plugins › {name}", action)

		def addPluginToVectorMenu(self, name, action):
			self._record(f"Vector › {name}", action)

		def addPluginToRasterMenu(self, name, action):
			self._record(f"Raster › {name}", action)

		def addPluginToDatabaseMenu(self, name, action):
			self._record(f"Database › {name}", action)

		def addPluginToWebMenu(self, name, action):
			self._record(f"Web › {name}", action)

		def removePluginMenu(self, name, action):
			pass

		removePluginVectorMenu = removePluginRasterMenu = removePluginDatabaseMenu = removePluginWebMenu = removePluginMenu

		# --- anything else a desktop plugin might reach for
		def __getattr__(self, name):
			return NULL

	return StandInInterface()
