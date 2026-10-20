// Toolbar and menu icons: Lucide (https://lucide.dev, ISC licence), imported one by one so only these
// are bundled. Laid out in QGIS Desktop's conventions, but not QGIS's own (GPL) icon set.

import {
	ArrowLeft, ArrowRight, CircleCheck, CirclePlus, Copy, Cpu, Expand, FileDown, FilePlus, FileUp, Focus, FolderOpen,
	Globe2, Hand, History, Info, Layers, LocateFixed, MapPinPlus, Magnet, Merge, Move, Package, Pencil, PencilOff,
	Pentagon, Puzzle, Redo2, RefreshCw, RotateCw, Route, Save, SaveAll, Scissors, Spline, SquareDashedMousePointer,
	SquareX, Table2, Trash2, Undo2, Waypoints, X, ZoomIn, ZoomOut, ChevronDown, ChevronRight, Eye, EyeOff,
	MousePointer2, Settings2, Search, Shapes, Map as MapIcon, Image,
} from "lucide";

export const ICONS = {
	"arrow-left": ArrowLeft, "arrow-right": ArrowRight, check: CircleCheck, "add-part": CirclePlus, copy: Copy,
	processing: Cpu, "zoom-full": Expand, "file-down": FileDown, "file-new": FilePlus, "file-up": FileUp,
	"zoom-selection": Focus, "folder-open": FolderOpen, globe: Globe2, pan: Hand, history: History, identify: Info,
	layers: Layers, "zoom-layer": LocateFixed, "add-point": MapPinPlus, magnet: Magnet, merge: Merge, move: Move,
	package: Package, edit: Pencil, "edit-off": PencilOff, "add-polygon": Pentagon, plugin: Puzzle, redo: Redo2,
	refresh: RefreshCw, rotate: RotateCw, "add-line": Route, save: Save, "save-edits": SaveAll, split: Scissors,
	trace: Spline, "select-box": SquareDashedMousePointer, deselect: SquareX, table: Table2, delete: Trash2,
	undo: Undo2, vertex: Waypoints, close: X, "zoom-in": ZoomIn, "zoom-out": ZoomOut, "chevron-down": ChevronDown,
	"chevron-right": ChevronRight, eye: Eye, "eye-off": EyeOff, select: MousePointer2, settings: Settings2,
	search: Search, "add-geometry": Shapes, map: MapIcon, raster: Image,
};
