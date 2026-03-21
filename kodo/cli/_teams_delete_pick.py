"""Team delete multiselect: space toggles one row; only ●/○ show checked state.

Questionary's default checkbox binds ``a`` to select-all/clear-all and ``i`` to invert; from an
empty selection both select every row, and the stock hint mislabels ``a`` as "toggle". The
default style also paints the whole label when checked, which looks like "everything selected".
"""

from __future__ import annotations

from typing import Any, List, Tuple

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style as PTStyle

from questionary import utils
from questionary.constants import (
    DEFAULT_QUESTION_PREFIX,
    DEFAULT_SELECTED_POINTER,
    INDICATOR_SELECTED,
    INDICATOR_UNSELECTED,
)
from questionary.prompts import common
from questionary.prompts.common import Choice, InquirerControl, Separator
from questionary.question import Question
from questionary.styles import merge_styles_default

_TEAMS_DELETE_INSTRUCTION = (
    "(↑↓ or j/k to move, space toggles ●/○ on this line, Enter confirms)"
)


class _TeamsDeleteInquirerControl(InquirerControl):
    """Like questionary's checkbox control, but labels stay plain; only markers show checked."""

    def _get_choice_tokens(self):
        tokens: List[Tuple[str, str]] = []

        def append(index: int, choice: Choice) -> None:
            selected = choice.value in self.selected_options

            if index == self.pointed_at:
                if self.pointer is not None:
                    tokens.append(("class:pointer", " {} ".format(self.pointer)))
                else:
                    tokens.append(("class:text", " " * 3))

                tokens.append(("[SetCursorPosition]", ""))
            else:
                pointer_length = len(self.pointer) if self.pointer is not None else 1
                tokens.append(("class:text", " " * (2 + pointer_length)))

            if isinstance(choice, Separator):
                tokens.append(("class:separator", "{}".format(choice.title)))
            elif choice.disabled:
                if isinstance(choice.title, list):
                    tokens.append(
                        ("class:selected" if selected else "class:disabled", "- ")
                    )
                    tokens.extend(choice.title)
                else:
                    tokens.append(
                        (
                            "class:selected" if selected else "class:disabled",
                            "- {}".format(choice.title),
                        )
                    )

                tokens.append(
                    (
                        "class:selected" if selected else "class:disabled",
                        "{}".format(
                            ""
                            if isinstance(choice.disabled, bool)
                            else " ({})".format(choice.disabled)
                        ),
                    )
                )
            else:
                shortcut = choice.get_shortcut_title() if self.use_shortcuts else ""

                if selected:
                    if self.use_indicator:
                        indicator = INDICATOR_SELECTED + " "
                    else:
                        indicator = ""

                    tokens.append(("class:selected", "{}".format(indicator)))
                else:
                    if self.use_indicator:
                        indicator = INDICATOR_UNSELECTED + " "
                    else:
                        indicator = ""

                    tokens.append(("class:text", "{}".format(indicator)))

                if isinstance(choice.title, list):
                    tokens.extend(choice.title)
                else:
                    tokens.append(("class:text", "{}{}".format(shortcut, choice.title)))

            tokens.append(("", "\n"))

        for i, c in enumerate(self.filtered_choices):
            append(i, c)

        current = self.get_pointed_at()

        if self.show_selected:
            answer = current.get_shortcut_title() if self.use_shortcuts else ""

            answer += (
                current.title if isinstance(current.title, str) else current.title[0][1]
            )

            tokens.append(("class:text", "  Answer: {}".format(answer)))

        show_description = self.show_description and current.description is not None
        if show_description:
            tokens.append(
                ("class:text", "  Description: {}".format(current.description))
            )

        if not (self.show_selected or show_description):
            tokens.pop()

        return tokens


def _ask_teams_delete_checkbox(message: str, choices: list[str]) -> list[Any] | None:
    """Multiselect like questionary.checkbox without ``a``/``i``; marker-only checked styling."""
    merged_style = merge_styles_default(
        [
            PTStyle([("bottom-toolbar", "noreverse")]),
            PTStyle([("selected", "fg:ansigreen bold")]),
        ]
    )

    ic = _TeamsDeleteInquirerControl(
        choices,
        default=None,
        pointer=DEFAULT_SELECTED_POINTER,
        initial_choice=None,
        show_description=False,
    )

    def get_prompt_tokens() -> List[Tuple[str, str]]:
        tokens: List[Tuple[str, str]] = []
        tokens.append(("class:qmark", DEFAULT_QUESTION_PREFIX))
        tokens.append(("class:question", " {} ".format(message)))

        if ic.is_answered:
            nbr_selected = len(ic.selected_options)
            if nbr_selected == 0:
                tokens.append(("class:answer", "done"))
            elif nbr_selected == 1:
                if isinstance(ic.get_selected_values()[0].title, list):
                    ts = ic.get_selected_values()[0].title
                    tokens.append(
                        (
                            "class:answer",
                            "".join([token[1] for token in ts]),  # type: ignore[arg-type]
                        )
                    )
                else:
                    tokens.append(
                        (
                            "class:answer",
                            "[{}]".format(ic.get_selected_values()[0].title),
                        )
                    )
            else:
                tokens.append(
                    ("class:answer", "done ({} selections)".format(nbr_selected))
                )
        else:
            tokens.append(("class:instruction", _TEAMS_DELETE_INSTRUCTION))
        return tokens

    def get_selected_values() -> List[Any]:
        return [c.value for c in ic.get_selected_values()]

    def perform_validation(_selected_values: List[str]) -> bool:
        ic.error_message = None
        return True

    layout = common.create_inquirer_layout(ic, get_prompt_tokens)

    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _abort(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add(" ", eager=True)
    def _toggle(_event):
        pointed_choice = ic.get_pointed_at().value
        if pointed_choice in ic.selected_options:
            ic.selected_options.remove(pointed_choice)
        else:
            ic.selected_options.append(pointed_choice)

        perform_validation(get_selected_values())

    def move_cursor_down(event):
        ic.select_next()
        while not ic.is_selection_valid():
            ic.select_next()

    def move_cursor_up(event):
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()

    bindings.add(Keys.Down, eager=True)(move_cursor_down)
    bindings.add(Keys.Up, eager=True)(move_cursor_up)
    bindings.add("j", eager=True)(move_cursor_down)
    bindings.add("k", eager=True)(move_cursor_up)
    bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
    bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

    @bindings.add(Keys.ControlM, eager=True)
    def _set_answer(event):
        selected_values = get_selected_values()
        ic.submission_attempted = True

        if perform_validation(selected_values):
            ic.is_answered = True
            event.app.exit(result=selected_values)

    @bindings.add(Keys.Any)
    def _other(_event):
        pass

    return Question(
        Application(
            layout=layout,
            key_bindings=bindings,
            style=merged_style,
            **utils.used_kwargs({}, Application.__init__),
        )
    ).ask()
