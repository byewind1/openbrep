"""Shared GDL keyword and built-in identifier sets.

These sets are intentionally conservative: they filter obvious GDL commands,
system variables, and formatter metadata from regex-based checkers. They are
not a substitute for a real parser.
"""

from __future__ import annotations


CONTROL_FLOW: frozenset[str] = frozenset({
    "IF", "THEN", "ELSE", "ENDIF", "FOR", "TO", "STEP", "NEXT",
    "WHILE", "ENDWHILE", "REPEAT", "UNTIL", "GOTO", "GOSUB", "RETURN",
    "EXIT", "END",
})

GROUP_COMMANDS: frozenset[str] = frozenset({
    "GROUP", "ENDGROUP", "SUBGROUP", "PLACEGROUP", "KILLGROUP",
    "GROUP_OPERATION", "OPERATION", "ISECTGROUP", "UNIONGROUP",
    "SUBGROUP", "ADDGROUP", "MULGROUP", "SECTGROUP",
})

GEOMETRY_COMMANDS: frozenset[str] = frozenset({
    "BLOCK", "SPHERE", "CONE", "CYLINDER", "CYLIND", "CYLIND_",
    "PRISM", "PRISM_", "BPRISM_", "CPRISM_", "FPRISM_", "SPRISM_",
    "PYRAMID", "REVOLVE", "REVOLVE_", "EXTRUDE", "EXTRUDE_", "RULED_",
    "MESH", "COONS", "TUBE", "TUBEA", "TUBEB", "TUBE_", "PLANE", "PLANE_",
    "PGON", "PGON_", "POLY", "POLY_", "POLY2_", "POLY2_B", "POLYROOF_",
    "MASS_", "XFORMR", "XFORM",
})

LOW_LEVEL_BODY_COMMANDS: frozenset[str] = frozenset({
    "BODY", "EDGE", "PGON", "VERT", "VECT", "TEVE",
    "HIDDENBODYEDGE", "HIDDENPROFILEEDGE", "SMOOTHBODYEDGE",
})

TRANSFORM_COMMANDS: frozenset[str] = frozenset({
    "ADD", "ADDX", "ADDY", "ADDZ", "ADD2", "MUL", "MUL2",
    "ROT", "ROTX", "ROTY", "ROTZ", "ROT2", "DEL", "DELN", "DELALL",
})

ATTRIBUTE_COMMANDS: frozenset[str] = frozenset({
    "PEN", "MATERIAL", "SECT_ATTRS", "LINE_TYPE", "FILL", "STYLE",
    "DEFINE", "USE", "MODEL", "WIRE", "SURFACE", "SOLID",
})

PARAMETER_COMMANDS: frozenset[str] = frozenset({
    "VALUES", "VALUE", "RANGE", "LOCK", "HIDEPARAMETER", "PARAMETERS",
    "DEFAULT", "VARTYPE",
})

TEXT_COMMANDS: frozenset[str] = frozenset({
    "TEXT", "TEXT2", "RICHTEXT2", "TEXTBLOCK", "PARAGRAPH", "ENDPARAGRAPH",
    "PEN", "STYLE", "DEFINE", "FONT", "ANCHOR", "FRAME", "TAB", "ALIGN",
})

TWO_D_COMMANDS: frozenset[str] = frozenset({
    "LINE", "LINE2", "RECT", "RECT2", "ARC", "ARC2", "CIRCLE", "CIRCLE2",
    "SPLINE", "SPLINE2", "HOTSPOT", "HOTSPOT2", "HOTLINE", "HOTLINE2",
    "HOTARC", "HOTARC2", "PROJECT2", "FRAGMENT2", "PICTURE2", "MARKER",
    "MARKER2", "POLY2", "POLY2_", "POLY2_B",
})

MISC_COMMANDS: frozenset[str] = frozenset({
    "PRINT", "ASSERT", "CALL", "MACRO", "RESOL", "TOLER",
    "CUTPLANE", "CUTFORM", "CUTPOLYA", "CUTPOLYX",
    "PUT", "GET", "NSP", "IND", "VARDIM1", "VARDIM2",
    "REQUEST", "INPUT", "OUTPUT", "FILE", "OPEN", "CLOSE",
})

BUILTIN_FUNCTIONS: frozenset[str] = frozenset({
    "SIN", "COS", "TAN", "ATN", "ACS", "ASN", "SQR", "ABS", "INT",
    "SGN", "EXP", "LOG", "LN", "NOT", "AND", "OR", "MOD", "DIV",
    "MIN", "MAX", "RND", "ROUND", "FRAC", "FIX",
    "VAL", "STR", "STR2", "SPLIT", "STRLEN", "STRSPN", "STRSUB",
    "STRSTR", "UPCASE", "DOWNCASE", "INFIX", "SUFFIX", "PREFIX",
    "CHR", "NUM",
})

SYSTEM_IDENTIFIERS: frozenset[str] = frozenset({
    "A", "B", "ZZYZX", "PI", "EPS", "TRUE", "FALSE", "APPLICATION",
    "VERSION", "UNID",
    "GLOB_SCALE", "GLOB_CH_SCALE", "GLOB_PAPER_SCALE",
    "GLOB_NORTH_DIR", "GLOB_ELEVATION", "GLOB_CONTEXT",
    "GLOB_FRAME_NR", "GLOB_CUTPLANE_H", "GLOB_CUTPLANE_T",
    "GLOB_CUTPLANES_INFO", "GLOB_CUTPLANES_INFO2",
    "GLOB_WORLD_ORIGO_OFFSET_X", "GLOB_WORLD_ORIGO_OFFSET_Y",
    "GLOB_MERIDIAN_CONVERGENCE", "GLOB_HSTORY_HEIGHT",
    "GLOB_HSTORY_ELEV", "GLOB_HSTORY_NR",
    "SYMB_LINETYPE", "SYMB_FILL", "SYMB_FILL_BG",
    "SYMB_SECT_FILL", "SYMB_SECT_FILL_BG",
    "SYMB_PEN", "SYMB_SECT_PEN", "SYMB_FRGROUND_PEN",
    "SYMB_LIN_PEN", "SYMB_FILL_PEN",
    "AC_SHOW_AREA", "AC_SHOW_VOLUME",
})

TYPE_CONVERSIONS: frozenset[str] = frozenset({"INCH", "MM", "CM", "M"})

OUTPUT_METADATA_WORDS: frozenset[str] = frozenset({
    "FILE", "SCRIPTS", "GDL", "PARAMLIST", "XML", "LENGTH", "INTEGER",
    "BOOLEAN", "MATERIAL", "REALNUM", "ANGLE", "STRING", "PENCOLOR",
    "FILLPATTERN", "LINETYPE",
})

GDL_BUILTINS: frozenset[str] = frozenset().union(
    CONTROL_FLOW,
    GROUP_COMMANDS,
    GEOMETRY_COMMANDS,
    LOW_LEVEL_BODY_COMMANDS,
    TRANSFORM_COMMANDS,
    ATTRIBUTE_COMMANDS,
    PARAMETER_COMMANDS,
    TEXT_COMMANDS,
    TWO_D_COMMANDS,
    MISC_COMMANDS,
    BUILTIN_FUNCTIONS,
    SYSTEM_IDENTIFIERS,
    TYPE_CONVERSIONS,
    OUTPUT_METADATA_WORDS,
)

GDL_BUILTINS_CASEFOLD: frozenset[str] = frozenset({item.upper() for item in GDL_BUILTINS})

GLOBAL_PREFIXES: tuple[str, ...] = ("gs_", "ac_", "GLOB_", "SYMB_")

