const TITLE_HISTORY_LIMIT = 50;

export type ScriptTitleState = {
  title: string;
  draft: string;
  editing: boolean;
  undoStack: string[];
  redoStack: string[];
};

export type ScriptTitleAction =
  | { type: "reset"; title: string }
  | { type: "begin-edit"; title: string }
  | { type: "set-draft"; title: string }
  | { type: "commit"; title: string; currentTitle: string }
  | { type: "cancel-edit"; title: string }
  | { type: "undo"; currentTitle: string }
  | { type: "redo"; currentTitle: string };

export const INITIAL_SCRIPT_TITLE_STATE: ScriptTitleState = {
  title: "",
  draft: "",
  editing: false,
  undoStack: [],
  redoStack: []
};

export function scriptTitleReducer(state: ScriptTitleState, action: ScriptTitleAction): ScriptTitleState {
  switch (action.type) {
    case "reset":
      return {
        title: action.title,
        draft: action.title,
        editing: false,
        undoStack: [],
        redoStack: []
      };
    case "begin-edit":
      return { ...state, draft: action.title, editing: true };
    case "set-draft":
      return { ...state, draft: action.title };
    case "commit":
      if (action.title === action.currentTitle) return { ...state, draft: action.title, editing: false };
      return {
        title: action.title,
        draft: action.title,
        editing: false,
        undoStack: [...state.undoStack, action.currentTitle].slice(-TITLE_HISTORY_LIMIT),
        redoStack: []
      };
    case "cancel-edit":
      return { ...state, draft: action.title, editing: false };
    case "undo": {
      if (!state.undoStack.length) return state;
      const previousTitle = state.undoStack[state.undoStack.length - 1];
      return {
        title: previousTitle,
        draft: previousTitle,
        editing: false,
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [action.currentTitle, ...state.redoStack].slice(0, TITLE_HISTORY_LIMIT)
      };
    }
    case "redo": {
      if (!state.redoStack.length) return state;
      const nextTitle = state.redoStack[0];
      return {
        title: nextTitle,
        draft: nextTitle,
        editing: false,
        undoStack: [...state.undoStack, action.currentTitle].slice(-TITLE_HISTORY_LIMIT),
        redoStack: state.redoStack.slice(1)
      };
    }
  }
}
