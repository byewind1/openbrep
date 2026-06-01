import type { Monaco } from '@monaco-editor/react'

export const GDL_LANGUAGE_ID = 'openbrep-gdl'
export const GDL_THEME_ID = 'openbrep-gdl-dark'

const CONTROL_KEYWORDS = [
  'IF',
  'THEN',
  'ELSE',
  'ENDIF',
  'FOR',
  'TO',
  'STEP',
  'NEXT',
  'WHILE',
  'DO',
  'ENDWHILE',
  'REPEAT',
  'UNTIL',
  'GOSUB',
  'RETURN',
  'END',
  'EXIT',
]

const GEOMETRY_COMMANDS = [
  'BLOCK',
  'BRICK',
  'CYLIND',
  'CONE',
  'SPHERE',
  'ELLIPS',
  'PRISM_',
  'PRISM',
  'EXTRUDE',
  'REVOLVE',
  'RULED',
  'TUBE',
  'SWEEP',
  'MESH',
  'COOR',
  'VERT',
  'EDGE',
  'PGON',
  'BODY',
  'GROUP',
  'ENDGROUP',
  'PLACEGROUP',
  'KILLGROUP',
]

const TRANSFORM_COMMANDS = ['ADD', 'ADDX', 'ADDY', 'ADDZ', 'ROT', 'ROTX', 'ROTY', 'ROTZ', 'MUL', 'MULX', 'MULY', 'MULZ', 'DEL']

const TWO_D_COMMANDS = [
  'PROJECT2',
  'LINE2',
  'RECT2',
  'POLY2_',
  'POLY2',
  'CIRCLE2',
  'ARC2',
  'TEXT2',
  'HOTSPOT2',
  'FRAGMENT2',
  'PICTURE2',
]

const PARAMETER_COMMANDS = [
  'VALUES',
  'RANGE',
  'PARAMETERS',
  'LOCK',
  'HIDEPARAMETER',
  'PARAMETERS',
  'VALUES{2}',
  'VALUES{3}',
]

const ATTRIBUTE_COMMANDS = [
  'PEN',
  'SECT_ATTRS',
  'MATERIAL',
  'DEFINE',
  'STYLE',
  'FILL',
  'LINE_TYPE',
  'TEXTURE',
  'SET',
  'BUILDING_MATERIAL',
]

const UI_COMMANDS = [
  'UI_DIALOG',
  'UI_PAGE',
  'UI_CURRENT_PAGE',
  'UI_BUTTON',
  'UI_INFIELD',
  'UI_OUTFIELD',
  'UI_PICT',
  'UI_STYLE',
  'UI_SEPARATOR',
  'UI_GROUPBOX',
]

const IO_COMMANDS = ['CALL', 'REQUEST', 'PUT', 'GET', 'USE', 'FILE_DEPENDENCE']

const FUNCTIONS = [
  'ABS',
  'ACS',
  'ASN',
  'ATN',
  'COS',
  'EXP',
  'INT',
  'LOG',
  'MAX',
  'MIN',
  'MOD',
  'RND',
  'SGN',
  'SIN',
  'SQR',
  'STR',
  'STRLEN',
  'TAN',
  'VARDIM1',
  'VARDIM2',
]

const FIXED_PARAMETERS = ['A', 'B', 'ZZYZX']

const COMMAND_SNIPPETS = [
  { label: 'BLOCK', insertText: 'BLOCK ${1:A}, ${2:B}, ${3:ZZYZX}', detail: '3D box primitive' },
  { label: 'CYLIND', insertText: 'CYLIND ${1:height}, ${2:radius}', detail: '3D cylinder primitive' },
  { label: 'PRISM_', insertText: 'PRISM_ ${1:n}, ${2:height},\n\t${3:x1}, ${4:y1},\n\t${5:x2}, ${6:y2}', detail: 'Extruded polygon' },
  { label: 'PROJECT2', insertText: 'PROJECT2 3, 270, 2', detail: '2D projection from 3D script' },
  { label: 'HOTSPOT2', insertText: 'HOTSPOT2 ${1:x}, ${2:y}', detail: '2D hotspot' },
  { label: 'FOR', insertText: 'FOR ${1:i} = ${2:1} TO ${3:count}\n\t${4:! body}\nNEXT ${1:i}', detail: 'Loop block' },
  { label: 'IF', insertText: 'IF ${1:condition} THEN\n\t${2:! body}\nENDIF', detail: 'Conditional block' },
  { label: 'CALL', insertText: 'CALL "${1:macro_name}" PARAMETERS ${2:all}', detail: 'Macro call' },
]

const ALL_COMMANDS = [
  ...CONTROL_KEYWORDS,
  ...GEOMETRY_COMMANDS,
  ...TRANSFORM_COMMANDS,
  ...TWO_D_COMMANDS,
  ...PARAMETER_COMMANDS,
  ...ATTRIBUTE_COMMANDS,
  ...UI_COMMANDS,
  ...IO_COMMANDS,
]

let registered = false

export function scriptLanguageForName(scriptName: string): string {
  if (scriptName.endsWith('.xml')) return 'xml'
  if (scriptName.endsWith('.gdl')) return GDL_LANGUAGE_ID
  return 'plaintext'
}

export function registerGdlLanguage(monaco: Monaco) {
  if (registered) return
  registered = true

  monaco.languages.register({ id: GDL_LANGUAGE_ID, aliases: ['GDL', 'gdl'], extensions: ['.gdl'] })
  monaco.languages.setLanguageConfiguration(GDL_LANGUAGE_ID, {
    comments: { lineComment: '!' },
    brackets: [
      ['(', ')'],
      ['[', ']'],
    ],
    autoClosingPairs: [
      { open: '"', close: '"' },
      { open: "'", close: "'" },
      { open: '(', close: ')' },
      { open: '[', close: ']' },
    ],
    surroundingPairs: [
      { open: '"', close: '"' },
      { open: "'", close: "'" },
      { open: '(', close: ')' },
      { open: '[', close: ']' },
    ],
    indentationRules: {
      increaseIndentPattern: /^\s*(IF\b.*\bTHEN|FOR\b|WHILE\b|REPEAT\b|GROUP\b|SUBROUTINE\b).*$/i,
      decreaseIndentPattern: /^\s*(ELSE|ENDIF|NEXT|ENDWHILE|UNTIL|ENDGROUP|RETURN|END)\b/i,
    },
  })
  monaco.languages.setMonarchTokensProvider(GDL_LANGUAGE_ID, {
    ignoreCase: true,
    defaultToken: '',
    tokenPostfix: '.gdl',
    controlKeywords: CONTROL_KEYWORDS,
    geometryCommands: GEOMETRY_COMMANDS,
    transformCommands: TRANSFORM_COMMANDS,
    twoDCommands: TWO_D_COMMANDS,
    parameterCommands: PARAMETER_COMMANDS,
    attributeCommands: ATTRIBUTE_COMMANDS,
    uiCommands: UI_COMMANDS,
    ioCommands: IO_COMMANDS,
    functions: FUNCTIONS,
    fixedParameters: FIXED_PARAMETERS,
    tokenizer: {
      root: [
        [/!.*/, 'comment'],
        [/"([^"\\]|\\.)*$/, 'string.invalid'],
        [/"/, { token: 'string.quote', bracket: '@open', next: '@stringDouble' }],
        [/'/, { token: 'string.quote', bracket: '@open', next: '@stringSingle' }],
        [/\b\d+(\.\d+)?([eE][+-]?\d+)?\b/, 'number'],
        [/[()[\],]/, 'delimiter'],
        [/[+\-*/^=<>&|]+/, 'operator'],
        [/[A-Za-z_][\w{}]*/, {
          cases: {
            '@controlKeywords': 'keyword.control',
            '@geometryCommands': 'keyword.geometry',
            '@transformCommands': 'keyword.transform',
            '@twoDCommands': 'keyword.2d',
            '@parameterCommands': 'keyword.parameter',
            '@attributeCommands': 'keyword.attribute',
            '@uiCommands': 'keyword.ui',
            '@ioCommands': 'keyword.io',
            '@functions': 'function',
            '@fixedParameters': 'variable.predefined',
            '/^GLOB_.*/': 'variable.global',
            '/^SYMB_.*/': 'variable.global',
            '/^AC_.*/': 'variable.archicad',
            '/^_.*/': 'variable.temporary',
            '@default': 'identifier',
          },
        }],
      ],
      stringDouble: [
        [/[^\\"]+/, 'string'],
        [/"/, { token: 'string.quote', bracket: '@close', next: '@pop' }],
      ],
      stringSingle: [
        [/[^\\']+/, 'string'],
        [/'/, { token: 'string.quote', bracket: '@close', next: '@pop' }],
      ],
    },
  })
  monaco.editor.defineTheme(GDL_THEME_ID, {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'comment.gdl', foreground: '6A9955', fontStyle: 'italic' },
      { token: 'keyword.control.gdl', foreground: 'C586C0' },
      { token: 'keyword.geometry.gdl', foreground: '4FC1FF', fontStyle: 'bold' },
      { token: 'keyword.transform.gdl', foreground: 'DCDCAA' },
      { token: 'keyword.2d.gdl', foreground: '9CDCFE' },
      { token: 'keyword.parameter.gdl', foreground: 'CE9178' },
      { token: 'keyword.attribute.gdl', foreground: 'B5CEA8' },
      { token: 'keyword.ui.gdl', foreground: 'C8A2C8' },
      { token: 'keyword.io.gdl', foreground: 'D7BA7D' },
      { token: 'function.gdl', foreground: 'DCDCAA' },
      { token: 'variable.predefined.gdl', foreground: 'FFCC66', fontStyle: 'bold' },
      { token: 'variable.global.gdl', foreground: '4EC9B0' },
      { token: 'variable.archicad.gdl', foreground: '4EC9B0' },
      { token: 'variable.temporary.gdl', foreground: 'B5CEA8' },
      { token: 'number.gdl', foreground: 'B5CEA8' },
      { token: 'string.gdl', foreground: 'CE9178' },
    ],
    colors: {
      'editor.background': '#0b111b',
      'editorLineNumber.foreground': '#526070',
      'editor.lineHighlightBackground': '#142033',
    },
  })
  monaco.languages.registerCompletionItemProvider(GDL_LANGUAGE_ID, {
    triggerCharacters: [' ', '_'],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      const commandSuggestions = COMMAND_SNIPPETS.map((item) => ({
        label: item.label,
        kind: monaco.languages.CompletionItemKind.Snippet,
        insertText: item.insertText,
        insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        detail: item.detail,
        range,
      }))
      const keywordSuggestions = ALL_COMMANDS.filter((command) => !COMMAND_SNIPPETS.some((item) => item.label === command)).map((command) => ({
        label: command,
        kind: monaco.languages.CompletionItemKind.Keyword,
        insertText: command,
        range,
      }))
      return { suggestions: [...commandSuggestions, ...keywordSuggestions] }
    },
  })
  monaco.languages.registerHoverProvider(GDL_LANGUAGE_ID, {
    provideHover(model, position) {
      const word = model.getWordAtPosition(position)?.word.toUpperCase()
      const snippet = COMMAND_SNIPPETS.find((item) => item.label === word)
      if (!snippet) return null
      return {
        contents: [
          { value: `**${snippet.label}**` },
          { value: snippet.detail },
        ],
      }
    },
  })
}
