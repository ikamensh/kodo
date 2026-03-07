# Habr Publishing Notes

## Approach

Habr's new editor (since 2023) has a **markdown mode** that accepts "Habr Flavored Markdown" (HFM).
The article was already 95% compatible — only two tweaks were needed:

1. Removed `text` language from the log code block (not in Habr's supported list)
2. Removed backticks from the `--improve` section header

## How to Paste

1. Open Habr publication editor
2. Switch to **markdown mode** (toggle at bottom of editor)
3. Paste contents of `article-ru-habr.md`
4. Preview before publishing

## References

- HFM syntax: https://habr.com/ru/docs/help/markdown/
- Markdown mode announcement: https://habr.com/ru/companies/habr/articles/725748/
- Supported code block languages: 1c, assembly, bash, css, cmake, coffeescript, cpp, cs, dart, delphi, diff, django, elixir, erlang, fsharp, go, haskell, java, javascript, json, julia, kotlin, lisp, lua, markdown, matlab, nginx, objectivec, perl, pgSQL, php, powershell, python, r, ruby, rust, scala, smalltalk, sql, swift, typescript, vala, vbscript, vhdl, xml, yaml
