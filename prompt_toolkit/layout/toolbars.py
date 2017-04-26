from __future__ import unicode_literals

from .containers import Window, ConditionalContainer
from .controls import BufferControl, TextFragmentsControl, UIControl, UIContent, UIControlKeyBindings
from .dimension import Dimension
from .lexers import SimpleLexer
from .processors import BeforeInput
from .utils import fragment_list_len
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.enums import SYSTEM_BUFFER, SearchDirection
from prompt_toolkit.filters import has_focus, has_arg, has_completions, has_validation_error, is_searching, Always, is_done, emacs_mode, vi_mode, vi_navigation_mode
from prompt_toolkit.filters import to_app_filter
from prompt_toolkit.key_binding.key_bindings import KeyBindings, merge_key_bindings, ConditionalKeyBindings
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys

__all__ = (
    'TextFragmentsToolbar',
    'ArgToolbar',
    'CompletionsToolbar',
    'SearchToolbar',
    'SystemToolbar',
    'ValidationToolbar',
)


class TextFragmentsToolbar(Window):
    def __init__(self, get_text_fragments, **kw):
        super(TextFragmentsToolbar, self).__init__(
            TextFragmentsControl(get_text_fragments, **kw),
            height=Dimension.exact(1))


class SystemToolbarControl(BufferControl):
    """
    :param enable: filter that enables the key bindings.
    """
    def __init__(self, loop, enable=True):
        self.enable = to_app_filter(enable)
        self.system_buffer = Buffer(name=SYSTEM_BUFFER, loop=loop)

        super(SystemToolbarControl, self).__init__(
            buffer=self.system_buffer,
            lexer=SimpleLexer(style='class:system-toolbar.text'),
            input_processor=BeforeInput.static('Shell command: ', 'class:system-toolbar'))

        self._global_bindings = self._build_global_key_bindings()
        self._bindings = self._build_key_bindings()

    def _build_global_key_bindings(self):
        focussed = has_focus(self.system_buffer)

        bindings = KeyBindings()

        @bindings.add(Keys.Escape, '!', filter= ~focussed & emacs_mode &
                self.enable)
        def _(event):
            " M-'!' will focus this user control. "
            event.app.layout.current_control = self

        @bindings.add('!', filter=~focussed & vi_mode & vi_navigation_mode)
        def _(event):
            " Focus. "
            event.app.vi_state.input_mode = InputMode.INSERT
            event.app.layout.current_control = self

        return bindings

    def _build_key_bindings(self):
        focussed = has_focus(self.system_buffer)

        # Emacs
        emacs_bindings = KeyBindings()
        handle = emacs_bindings.add

        @handle('escape', filter=focussed)
        @handle('c-g', filter=focussed)
        @handle('c-c', filter=focussed)
        def _(event):
            " Hide system prompt. "
            self.system_buffer.reset()
            event.app.layout.pop_focus()

        @handle('enter', filter=focussed)
        def _(event):
            " Run system command. "
            event.app.run_system_command(self.system_buffer.text)
            self.system_buffer.reset(append_to_history=True)
            event.app.layout.pop_focus()

        # Vi.
        vi_bindings = KeyBindings()
        handle = vi_bindings.add

        @handle('escape', filter=focussed)
        @handle('c-c', filter=focussed)
        def _(event):
            " Hide system prompt. "
            event.app.vi_state.input_mode = InputMode.NAVIGATION
            self.system_buffer.reset()
            event.app.layout.pop_focus()

        @handle('enter', filter=focussed)
        def _(event):
            " Run system command. "
            event.app.vi_state.input_mode = InputMode.NAVIGATION
            event.app.run_system_command(self.system_buffer.text)
            self.system_buffer.reset(append_to_history=True)
            event.app.layout.pop_focus()

        return merge_key_bindings([
            ConditionalKeyBindings(emacs_bindings, emacs_mode),
            ConditionalKeyBindings(vi_bindings, vi_mode),
        ])

    def get_key_bindings(self, app):
        return UIControlKeyBindings(
            global_key_bindings=self._global_bindings,
            key_bindings=self._bindings,
            modal=False)


class SystemToolbar(ConditionalContainer):
    def __init__(self, loop, enable=True):
        self.control = SystemToolbarControl(loop=loop, enable=enable)
        super(SystemToolbar, self).__init__(
            content=Window(self.control,
                height=Dimension.exact(1),
                style='class:system-toolbar'),
            filter=has_focus(self.control.system_buffer) & ~is_done)


class ArgToolbarControl(TextFragmentsControl):
    def __init__(self):
        def get_text_fragments(app):
            arg = app.key_processor.arg
            if arg == '-':
                arg = '-1'

            return [
                ('class:arg-toolbar', 'Repeat: '),
                ('class:arg-toolbar.text', arg),
            ]

        super(ArgToolbarControl, self).__init__(get_text_fragments)


class ArgToolbar(ConditionalContainer):
    def __init__(self):
        super(ArgToolbar, self).__init__(
            content=Window(
                ArgToolbarControl(),
                height=Dimension.exact(1)),
            filter=has_arg)


class SearchToolbarControl(BufferControl):
    """
    :param vi_mode: Display '/' and '?' instead of I-search.
    """
    def __init__(self, search_buffer, vi_mode=False):
        assert isinstance(search_buffer, Buffer)

        def get_before_input(app):
            if not is_searching(app):
                text = ''
            elif app.current_search_state.direction == SearchDirection.BACKWARD:
                text = ('?' if vi_mode else 'I-search backward: ')
            else:
                text = ('/' if vi_mode else 'I-search: ')

            return [('class:search-toolbar', text)]

        super(SearchToolbarControl, self).__init__(
            buffer=search_buffer,
            input_processor=BeforeInput(get_before_input),
            lexer=SimpleLexer(
                style='class:search-toolbar.text'))


class SearchToolbar(ConditionalContainer):
    def __init__(self, search_buffer, vi_mode=False):
        control = SearchToolbarControl(search_buffer, vi_mode=vi_mode)
        super(SearchToolbar, self).__init__(
            content=Window(control, height=Dimension.exact(1),
                           style='class:search-toolbar'),
            filter=is_searching & ~is_done)

        self.control = control


class CompletionsToolbarControl(UIControl):
    def create_content(self, app, width, height):
        complete_state = app.current_buffer.complete_state
        if complete_state:
            completions = complete_state.current_completions
            index = complete_state.complete_index  # Can be None!

            # Width of the completions without the left/right arrows in the margins.
            content_width = width - 6

            # Booleans indicating whether we stripped from the left/right
            cut_left = False
            cut_right = False

            # Create Menu content.
            fragments = []

            for i, c in enumerate(completions):
                # When there is no more place for the next completion
                if fragment_list_len(fragments) + len(c.display) >= content_width:
                    # If the current one was not yet displayed, page to the next sequence.
                    if i <= (index or 0):
                        fragments = []
                        cut_left = True
                    # If the current one is visible, stop here.
                    else:
                        cut_right = True
                        break

                fragments.append(('class:completion,current-completion' if i == index
                               else 'class:completion', c.display))
                fragments.append(('', ' '))

            # Extend/strip until the content width.
            fragments.append(('', ' ' * (content_width - fragment_list_len(fragments))))
            fragments = fragments[:content_width]

            # Return fragments
            all_fragments = [
                ('', ' '),
                ('class:completions-toolbar.arrow', '<' if cut_left else ' '),
                ('', ' '),
            ] + fragments + [
                ('', ' '),
                ('class:completions-toolbar.arrow', '>' if cut_right else ' '),
                ('', ' '),
            ]
        else:
            all_fragments = []

        def get_line(i):
            return all_fragments

        return UIContent(get_line=get_line, line_count=1)


class CompletionsToolbar(ConditionalContainer):
    def __init__(self, extra_filter=Always()):
        super(CompletionsToolbar, self).__init__(
            content=Window(
                CompletionsToolbarControl(),
                height=Dimension.exact(1),
                style='class:completions-toolbar'),
            filter=has_completions & ~is_done & extra_filter)


class ValidationToolbarControl(TextFragmentsControl):
    def __init__(self, show_position=False):
        def get_text_fragments(app):
            buff = app.current_buffer

            if buff.validation_error:
                row, column = buff.document.translate_index_to_position(
                    buff.validation_error.cursor_position)

                if show_position:
                    text = '%s (line=%s column=%s)' % (
                        buff.validation_error.message, row + 1, column + 1)
                else:
                    text = buff.validation_error.message

                return [('class:validation-toolbar', text)]
            else:
                return []

        super(ValidationToolbarControl, self).__init__(get_text_fragments)


class ValidationToolbar(ConditionalContainer):
    def __init__(self, show_position=False):
        super(ValidationToolbar, self).__init__(
            content=Window(
                ValidationToolbarControl(show_position=show_position),
                height=Dimension.exact(1)),
            filter=has_validation_error & ~is_done)


# Deprecated aliases.
TokenListToolbar = TextFragmentsToolbar
