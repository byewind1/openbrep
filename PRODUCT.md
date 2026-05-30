# Product

## Register

product

## Users

OpenBrep serves Archicad users, GDL developers, and advanced BIM/CAD users who
work with HSF/GDL sources and need fast code, preview, diagnostics, compile, and
AI-assisted iteration.

## Product Purpose

OpenBrep is a professional AI-assisted GDL workbench. It manages HSF-native
source projects, supports AI generation/modification/explanation, verifies
compile output, and keeps project artifacts, revisions, and memory traceable.
Success means a user can keep a real GDL object project open for long-running
work without falling back to a generic chatbot or a crowded settings form.

## Brand Personality

Expert, quiet, precise.

The interface should feel closer to a professional development workbench such as
VS Code, Blender, or a CAD authoring tool than to a Streamlit form.

## Anti-references

- Crowded Streamlit-style sidebars full of text, buttons, and stacked controls.
- Generic chatbot shells where the editable HSF/GDL project is secondary.
- Marketing-page layouts, decorative cards, or large explanatory copy inside the
  workbench.
- Treating `.gsm` artifacts as editable source instead of compiled output.

## Design Principles

- Code and source lifecycle first: make editable HSF/GDL state visible and
  trustworthy.
- Context-driven workspace: code, preview, parameters, diagnostics, or history
  can become the main working context depending on the task.
- Thin UI, deep behavior: controls should expose real project operations without
  duplicating domain logic in React.
- Traceable AI collaboration: assistant output should be reviewable, adoptable,
  and reversible rather than silently mutating source.
- Professional density: compact panels, clear hierarchy, and minimal explanatory
  text beat form-like clutter.

## Accessibility & Inclusion

Target practical WCAG AA contrast for text and controls. Keep keyboard and screen
reader affordances for dialogs, buttons, and editor-adjacent workflows. Avoid
motion-dependent interactions; future animation should respect reduced-motion
preferences.
