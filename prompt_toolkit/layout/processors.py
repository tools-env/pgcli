"""
Processors are little transformation blocks that transform the fragments list
from a buffer before the BufferControl will render it to the screen.

They can insert fragments before or after, or highlight fragments by replacing the
fragment types.
"""
from __future__ import unicode_literals
from abc import ABCMeta, abstractmethod
from six import with_metaclass
from six.moves import range

from prompt_toolkit.cache import SimpleCache
from prompt_toolkit.document import Document
from prompt_toolkit.enums import SearchDirection
from prompt_toolkit.filters import to_app_filter, ViInsertMultipleMode
from prompt_toolkit.layout.utils import fragment_list_to_text
from prompt_toolkit.reactive import Integer

from .utils import fragment_list_len, explode_text_fragments

import re

__all__ = (
    'Processor',
    'TransformationInput',
    'Transformation',

    'DummyProcessor',
    'HighlightSearchProcessor',
    'HighlightSelectionProcessor',
    'PasswordProcessor',
    'HighlightMatchingBracketProcessor',
    'DisplayMultipleCursors',
    'BeforeInput',
    'ShowArg',
    'AfterInput',
    'AppendAutoSuggestion',
    'ConditionalProcessor',
    'ShowLeadingWhiteSpaceProcessor',
    'ShowTrailingWhiteSpaceProcessor',
    'TabsProcessor',
    'DynamicProcessor',
    'merge_processors',
)


class Processor(with_metaclass(ABCMeta, object)):
    """
    Manipulate the fragments for a given line in a
    :class:`~prompt_toolkit.layout.controls.BufferControl`.
    """
    @abstractmethod
    def apply_transformation(self, transformation_input):
        """
        Apply transformation. Returns a :class:`.Transformation` instance.

        :param transformation_input: :class:`.TransformationInput` object.
        """
        return Transformation(transformation_input.fragments)


class TransformationInput(object):
    """
    :param app: :class:`.Application` instance.
    :param control: :class:`.BufferControl` instance.
    :param lineno: The number of the line to which we apply the processor.
    :param source_to_display: A function that returns the position in the
        `fragments` for any position in the source string. (This takes
        previous processors into account.)
    :param fragments: List of fragments that we can transform. (Received from the
        previous processor.)
    """
    def __init__(self, app, buffer_control, document, lineno,
                 source_to_display, fragments, width, height):
        self.app = app
        self.buffer_control = buffer_control
        self.document = document
        self.lineno = lineno
        self.source_to_display = source_to_display
        self.fragments = fragments
        self.width = width
        self.height = height

    def unpack(self):
        return (self.app, self.buffer_control, self.document, self.lineno,
                self.source_to_display, self.fragments, self.width, self.height)


class Transformation(object):
    """
    Transformation result, as returned by :meth:`.Processor.apply_transformation`.

    Important: Always make sure that the length of `document.text` is equal to
               the length of all the text in `fragments`!

    :param fragments: The transformed fragments. To be displayed, or to pass to the
        next processor.
    :param source_to_display: Cursor position transformation from original string to
        transformed string.
    :param display_to_source: Cursor position transformed from source string to
        original string.
    """
    def __init__(self, fragments, source_to_display=None, display_to_source=None):
        self.fragments = fragments
        self.source_to_display = source_to_display or (lambda i: i)
        self.display_to_source = display_to_source or (lambda i: i)


class DummyProcessor(Processor):
    """
    A `Processor` that doesn't do anything.
    """
    def apply_transformation(self, transformation_input):
        return Transformation(transformation_input.fragments)


class HighlightSearchProcessor(Processor):
    """
    Processor that highlights search matches in the document.
    Note that this doesn't support multiline search matches yet.

    :param preview_search: A Filter; when active it indicates that we take
        the search text in real time while the user is typing, instead of the
        last active search state.
    """
    def __init__(self, preview_search=False):
        self.preview_search = to_app_filter(preview_search)

    def _get_search_text(self, app, buffer_control):
        """
        The text we are searching for.
        """
        # When the search buffer has focus, take that text.
        if self.preview_search(app):
            search_buffer = buffer_control.search_buffer
            if search_buffer is not None and search_buffer.text:
                return search_buffer.text

        # Otherwise, take the text of the last active search.
        return buffer_control.search_state.text

    def apply_transformation(self, transformation_input):
        app, buffer_control, document, lineno, source_to_display, fragments, _, _ = transformation_input.unpack()

        search_text = self._get_search_text(app, buffer_control)
        searchmatch_current_fragment = ' class:search-match.current '
        searchmatch_fragment = ' class:search-match '

        if search_text and not app.is_done:
            # For each search match, replace the style string.
            line_text = fragment_list_to_text(fragments)
            fragments = explode_text_fragments(fragments)

            if buffer_control.search_state.ignore_case():
                flags = re.IGNORECASE
            else:
                flags = 0

            # Get cursor column.
            if document.cursor_position_row == lineno:
                cursor_column = source_to_display(document.cursor_position_col)
            else:
                cursor_column = None

            for match in re.finditer(re.escape(search_text), line_text, flags=flags):
                if cursor_column is not None:
                    on_cursor = match.start() <= cursor_column < match.end()
                else:
                    on_cursor = False

                for i in range(match.start(), match.end()):
                    old_fragment, text = fragments[i]
                    if on_cursor:
                        fragments[i] = (old_fragment + searchmatch_current_fragment, fragments[i][1])
                    else:
                        fragments[i] = (old_fragment + searchmatch_fragment, fragments[i][1])

        return Transformation(fragments)


class HighlightSelectionProcessor(Processor):
    """
    Processor that highlights the selection in the document.
    """
    def apply_transformation(self, transformation_input):
        app, buffer_control, document, lineno, source_to_display, fragments, _, _ = transformation_input.unpack()

        selected_fragment = ' class:selected '

        # In case of selection, highlight all matches.
        selection_at_line = document.selection_range_at_line(lineno)

        if selection_at_line:
            from_, to = selection_at_line
            from_ = source_to_display(from_)
            to = source_to_display(to)

            fragments = explode_text_fragments(fragments)

            if from_ == 0 and to == 0 and len(fragments) == 0:
                # When this is an empty line, insert a space in order to
                # visualiase the selection.
                return Transformation([(selected_fragment, ' ')])
            else:
                for i in range(from_, to + 1):
                    if i < len(fragments):
                        old_fragment, old_text = fragments[i]
                        fragments[i] = (old_fragment + selected_fragment, old_text)

        return Transformation(fragments)


class PasswordProcessor(Processor):
    """
    Processor that turns masks the input. (For passwords.)

    :param char: (string) Character to be used. "*" by default.
    """
    def __init__(self, char='*'):
        self.char = char

    def apply_transformation(self, ti):
        fragments = [(style, self.char * len(text)) for style, text in ti.fragments]
        return Transformation(fragments)


class HighlightMatchingBracketProcessor(Processor):
    """
    When the cursor is on or right after a bracket, it highlights the matching
    bracket.

    :param max_cursor_distance: Only highlight matching brackets when the
        cursor is within this distance. (From inside a `Processor`, we can't
        know which lines will be visible on the screen. But we also don't want
        to scan the whole document for matching brackets on each key press, so
        we limit to this value.)
    """
    _closing_braces = '])}>'

    def __init__(self, chars='[](){}<>', max_cursor_distance=1000):
        self.chars = chars
        self.max_cursor_distance = max_cursor_distance

        self._positions_cache = SimpleCache(maxsize=8)

    def _get_positions_to_highlight(self, document):
        """
        Return a list of (row, col) tuples that need to be highlighted.
        """
        # Try for the character under the cursor.
        if document.current_char and document.current_char in self.chars:
            pos = document.find_matching_bracket_position(
                    start_pos=document.cursor_position - self.max_cursor_distance,
                    end_pos=document.cursor_position + self.max_cursor_distance)

        # Try for the character before the cursor.
        elif (document.char_before_cursor and document.char_before_cursor in
              self._closing_braces and document.char_before_cursor in self.chars):
            document = Document(document.text, document.cursor_position - 1)

            pos = document.find_matching_bracket_position(
                    start_pos=document.cursor_position - self.max_cursor_distance,
                    end_pos=document.cursor_position + self.max_cursor_distance)
        else:
            pos = None

        # Return a list of (row, col) tuples that need to be highlighted.
        if pos:
            pos += document.cursor_position  # pos is relative.
            row, col = document.translate_index_to_position(pos)
            return [(row, col), (document.cursor_position_row, document.cursor_position_col)]
        else:
            return []

    def apply_transformation(self, transformation_input):
        app, buffer_control, document, lineno, source_to_display, fragments, _, _ = transformation_input.unpack()

        # Get the highlight positions.
        key = (app.render_counter, document.text, document.cursor_position)
        positions = self._positions_cache.get(
            key, lambda: self._get_positions_to_highlight(document))

        # Apply if positions were found at this line.
        if positions:
            for row, col in positions:
                if row == lineno:
                    col = source_to_display(col)
                    fragments = explode_text_fragments(fragments)
                    style, text = fragments[col]

                    if col == document.cursor_position_col:
                        style += ' class:matching-bracket.cursor '
                    else:
                        style += ' class:matching-bracket.other '

                    fragments[col] = (style, text)

        return Transformation(fragments)


class DisplayMultipleCursors(Processor):
    """
    When we're in Vi block insert mode, display all the cursors.
    """
    _insert_multiple =  ViInsertMultipleMode()

    def apply_transformation(self, transformation_input):
        app, buffer_control, document, lineno, source_to_display, fragments, _, _ = transformation_input.unpack()

        buff = buffer_control.buffer

        if self._insert_multiple(app):
            positions = buff.multiple_cursor_positions
            fragments = explode_text_fragments(fragments)

            # If any cursor appears on the current line, highlight that.
            start_pos = document.translate_row_col_to_index(lineno, 0)
            end_pos = start_pos + len(document.lines[lineno])

            fragment_suffix = ' class:multiple-cursors.cursor '

            for p in positions:
                if start_pos <= p < end_pos:
                    column = source_to_display(p - start_pos)

                    # Replace fragment.
                    style, text = fragments[column]
                    style += fragment_suffix
                    fragments[column] = (style, text)
                elif p == end_pos:
                    fragments.append((fragment_suffix, ' '))

            return Transformation(fragments)
        else:
            return Transformation(fragments)


class BeforeInput(Processor):
    """
    Insert fragments before the input.

    :param get_text_fragments: Callable that takes a
        :class:`~prompt_toolkit.application.Application` and returns the
        list of fragments to be inserted.
    """
    def __init__(self, get_text_fragments):
        assert callable(get_text_fragments)
        self.get_text_fragments = get_text_fragments

    def apply_transformation(self, ti):
        if ti.lineno == 0:
            fragments_before = self.get_text_fragments(ti.app)
            fragments = fragments_before + ti.fragments

            shift_position = fragment_list_len(fragments_before)
            source_to_display = lambda i: i + shift_position
            display_to_source = lambda i: i - shift_position
        else:
            fragments = ti.fragments
            source_to_display = None
            display_to_source = None

        return Transformation(fragments, source_to_display=source_to_display,
                              display_to_source=display_to_source)

    @classmethod
    def static(cls, text, style=''):
        """
        Create a :class:`.BeforeInput` instance that always inserts the same
        text.
        """
        def get_static_fragments(app):
            return [(style, text)]
        return cls(get_static_fragments)

    def __repr__(self):
        return 'BeforeInput(get_text_fragments=%r)' % (self.get_text_fragments, )


class ShowArg(BeforeInput):
    def __init__(self):
        super(ShowArg, self).__init__(self._get_text_fragments)

    def _get_text_fragments(self, app):
        if app.key_processor.arg is None:
            return []
        else:
            arg = app.key_processor.arg

            return [
                ('prompt.arg', '(arg: '),
                ('prompt.arg.text', str(arg)),
                ('prompt.arg', ') '),
            ]

    def __repr__(self):
        return 'ShowArg()'


class AfterInput(Processor):
    """
    Insert fragments after the input.

    :param get_text_fragments: Callable that takes a
        :class:`~prompt_toolkit.application.Application` and returns the
        list of fragments to be appended.
    """
    def __init__(self, get_text_fragments):
        assert callable(get_text_fragments)
        self.get_text_fragments = get_text_fragments

    def apply_transformation(self, ti):
        # Insert fragments after the last line.
        if ti.lineno == ti.document.line_count - 1:
            return Transformation(fragments=ti.fragments + self.get_text_fragments(ti.app))
        else:
            return Transformation(fragments=ti.fragments)

    @classmethod
    def static(cls, text, style=''):
        """
        Create a :class:`.AfterInput` instance that always inserts the same
        text.
        """
        def get_static_fragments(app):
            return [(style, text)]
        return cls(get_static_fragments)

    def __repr__(self):
        return '%s(get_text_fragments=%r)' % (
            self.__class__.__name__, self.get_text_fragments)


class AppendAutoSuggestion(Processor):
    """
    Append the auto suggestion to the input.
    (The user can then press the right arrow the insert the suggestion.)
    """
    def __init__(self, style='class:auto-suggestion'):
        self.style = style

    def apply_transformation(self, ti):
        # Insert fragments after the last line.
        if ti.lineno == ti.document.line_count - 1:
            buffer = ti.buffer_control.buffer

            if buffer.suggestion and ti.document.is_cursor_at_the_end:
                suggestion = buffer.suggestion.text
            else:
                suggestion = ''

            return Transformation(fragments=ti.fragments + [(self.style, suggestion)])
        else:
            return Transformation(fragments=ti.fragments)


class ShowLeadingWhiteSpaceProcessor(Processor):
    """
    Make leading whitespace visible.

    :param get_char: Callable that takes a :class:`Application`
        instance and returns one character.
    """
    def __init__(self, get_char=None, style='class:leading-whitespace'):
        assert get_char is None or callable(get_char)

        if get_char is None:
            def get_char(app):
                if '\xb7'.encode(app.output.encoding(), 'replace') == b'?':
                    return '.'
                else:
                    return '\xb7'

        self.style = style
        self.get_char = get_char

    def apply_transformation(self, ti):
        app = ti.app
        fragments = ti.fragments

        # Walk through all te fragments.
        if fragments and fragment_list_to_text(fragments).startswith(' '):
            t = (self.style, self.get_char(app))
            fragments = explode_text_fragments(fragments)

            for i in range(len(fragments)):
                if fragments[i][1] == ' ':
                    fragments[i] = t
                else:
                    break

        return Transformation(fragments)


class ShowTrailingWhiteSpaceProcessor(Processor):
    """
    Make trailing whitespace visible.

    :param get_char: Callable that takes a :class:`Application`
        instance and returns one character.
    """
    def __init__(self, get_char=None, style='class:training-whitespace'):
        assert get_char is None or callable(get_char)

        if get_char is None:
            def get_char(app):
                if '\xb7'.encode(app.output.encoding(), 'replace') == b'?':
                    return '.'
                else:
                    return '\xb7'

        self.style = style
        self.get_char = get_char

    def apply_transformation(self, ti):
        app = ti.app
        fragments = ti.fragments

        if fragments and fragments[-1][1].endswith(' '):
            t = (self.style, self.get_char(app))
            fragments = explode_text_fragments(fragments)

            # Walk backwards through all te fragments and replace whitespace.
            for i in range(len(fragments) - 1, -1, -1):
                char = fragments[i][1]
                if char == ' ':
                    fragments[i] = t
                else:
                    break

        return Transformation(fragments)


class TabsProcessor(Processor):
    """
    Render tabs as spaces (instead of ^I) or make them visible (for instance,
    by replacing them with dots.)

    :param tabstop: (Integer) Horizontal space taken by a tab.
    :param get_char1: Callable that takes a `Application` and return a
        character (text of length one). This one is used for the first space
        taken by the tab.
    :param get_char2: Like `get_char1`, but for the rest of the space.
    """
    def __init__(self, tabstop=4, get_char1=None, get_char2=None,
                 style='class:tab'):
        assert isinstance(tabstop, Integer)
        assert get_char1 is None or callable(get_char1)
        assert get_char2 is None or callable(get_char2)

        self.get_char1 = get_char1 or get_char2 or (lambda app: '|')
        self.get_char2 = get_char2 or get_char1 or (lambda app: '\u2508')
        self.tabstop = tabstop
        self.style = style

    def apply_transformation(self, ti):
        app = ti.app

        tabstop = int(self.tabstop)
        style = self.style

        # Create separator for tabs.
        separator1 = self.get_char1(app)
        separator2 = self.get_char2(app)

        # Transform fragments.
        fragments = explode_text_fragments(ti.fragments)

        position_mappings = {}
        result_fragments = []
        pos = 0

        for i, fragment_and_text in enumerate(fragments):
            position_mappings[i] = pos

            if fragment_and_text[1] == '\t':
                # Calculate how many characters we have to insert.
                count = tabstop - (pos % tabstop)
                if count == 0:
                    count = tabstop

                # Insert tab.
                result_fragments.append((style, separator1))
                result_fragments.append((style, separator2 * (count - 1)))
                pos += count
            else:
                result_fragments.append(fragment_and_text)
                pos += 1

        position_mappings[len(fragments)] = pos

        def source_to_display(from_position):
            " Maps original cursor position to the new one. "
            return position_mappings[from_position]

        def display_to_source(display_pos):
            " Maps display cursor position to the original one. "
            position_mappings_reversed = dict((v, k) for k, v in position_mappings.items())

            while display_pos >= 0:
                try:
                    return position_mappings_reversed[display_pos]
                except KeyError:
                    display_pos -= 1
            return 0

        return Transformation(
            result_fragments,
            source_to_display=source_to_display,
            display_to_source=display_to_source)


class ReverseSearchProcessor(Processor):
    """
    Process to display the "(reverse-i-search)`...`:..." stuff around
    the search buffer.

    Note: This processor is meant to be applied to the BufferControl that
    contains the search buffer, it's not meant for the original input.
    """
    _excluded_input_processors = [
        HighlightSearchProcessor,
        HighlightSelectionProcessor,
        HighlightSelectionProcessor,
        BeforeInput,
        AfterInput,
    ]

    def _get_main_buffer(self, app, buffer_control):
        from prompt_toolkit.layout.controls import BufferControl
        prev_control = app.layout.previous_control
        if isinstance(prev_control, BufferControl) and \
                prev_control.search_buffer_control == buffer_control:
            return prev_control, prev_control.search_state
        return None, None

    def _content(self, main_control, ti):
        from prompt_toolkit.layout.controls import BufferControl

        # Emulate the BufferControl through which we are searching.
        # For this we filter out some of the input processors.
        excluded_processors = tuple(self._excluded_input_processors)

        def filter_processor(item):
            """ Filter processors from the main control that we want to disable
            here. This returns either an accepted processor or None. """
            # For a `_MergedProcessor`, check each individual processor, recursively.
            if isinstance(item, _MergedProcessor):
                accepted_processors = [filter_processor(p) for p in item.processors]
                accepted_processors = [p for p in accepted_processors if p is not None]

                if len(accepted_processors) > 1:
                    return _MergedProcessor(accepted_processors)
                elif accepted_processors == 1:
                    return accepted_processors[0]

            # For a `ConditionalProcessor`, check the body.
            elif isinstance(item, ConditionalProcessor):
                p = filter_processor(item.processor)
                if p:
                    return ConditionalProcessor(p, item.filter)

            # Otherwise, check the processor itself.
            else:
                if not isinstance(item, excluded_processors):
                    return item

        filtered_processor = filter_processor(main_control.input_processor)
        highlight_processor = HighlightSearchProcessor(preview_search=True)

        if filtered_processor:
            new_processor = _MergedProcessor([filtered_processor, highlight_processor])
        else:
            new_processor = highlight_processor

        buffer_control = BufferControl(
                 buffer=main_control.buffer,
                 input_processor=new_processor,
                 lexer=main_control.lexer,
                 preview_search=True,
                 search_buffer_control=ti.buffer_control)

        return buffer_control.create_content(ti.app, ti.width, ti.height)


    def apply_transformation(self, ti):
        main_control, search_state = self._get_main_buffer(ti.app, ti.buffer_control)

        if ti.lineno == 0 and main_control:
            content = self._content(main_control, ti)

            # Get the line from the original document for this search.
            line_fragments = content.get_line(
                main_control.buffer.document_for_search(search_state).cursor_position_row)

            if search_state.direction == SearchDirection.FORWARD:
                direction_text = 'i-search'
            else:
                direction_text = 'reverse-i-search'

            fragments_before = [
                ('class:prompt.search', '('),
                ('class:prompt.search', direction_text),
                ('class:prompt.search', ')`'),
            ]

            fragments = fragments_before + [
                ('class:prompt.search.text', fragment_list_to_text(ti.fragments)),
                ('', "': "),
            ] + line_fragments

            shift_position = fragment_list_len(fragments_before)
            source_to_display = lambda i: i + shift_position
            display_to_source = lambda i: i - shift_position
        else:
            source_to_display = None
            display_to_source = None
            fragments = ti.fragments

        return Transformation(fragments, source_to_display=source_to_display,
                              display_to_source=display_to_source)


class ConditionalProcessor(Processor):
    """
    Processor that applies another processor, according to a certain condition.
    Example::

        # Create a function that returns whether or not the processor should
        # currently be applied.
        def highlight_enabled(app):
            return true_or_false

        # Wrapt it in a `ConditionalProcessor` for usage in a `BufferControl`.
        BufferControl(input_processors=[
            ConditionalProcessor(HighlightSearchProcessor(),
                                 Condition(highlight_enabled))])

    :param processor: :class:`.Processor` instance.
    :param filter: :class:`~prompt_toolkit.filters.AppFilter` instance.
    """
    def __init__(self, processor, filter):
        assert isinstance(processor, Processor)

        self.processor = processor
        self.filter = to_app_filter(filter)

    def apply_transformation(self, transformation_input):
        # Run processor when enabled.
        if self.filter(transformation_input.app):
            return self.processor.apply_transformation(transformation_input)
        else:
            return Transformation(transformation_input.fragments)

    def __repr__(self):
        return '%s(processor=%r, filter=%r)' % (
            self.__class__.__name__, self.processor, self.filter)


class DynamicProcessor(Processor):
    """
    Processor class that can dynamically returns any Processor.

    :param get_processor: Callable that returns a :class:`.Processor` instance.
    """
    def __init__(self, get_processor):
        assert callable(get_processor)
        self.get_processor = get_processor

    def apply_transformation(self, ti):
        processor = self.get_processor() or DummyProcessor()
        return processor.apply_transformation(ti)


def merge_processors(processors):
    """
    Merge multiple `Processor` objects into one.
    """
    return _MergedProcessor(processors)


class _MergedProcessor(Processor):
    """
    Processor that groups multiple other `Processor` objects, but exposes an
    API as if it is one `Processor`.
    """
    def __init__(self, processors):
        assert all(isinstance(p, Processor) for p in processors)
        self.processors = processors

    def apply_transformation(self, ti):
        source_to_display_functions = [ti.source_to_display]
        display_to_source_functions = []
        fragments = ti.fragments

        def source_to_display(i):
            """ Translate x position from the buffer to the x position in the
            processor fragments list. """
            for f in source_to_display_functions:
                i = f(i)
            return i

        for p in self.processors:
            transformation = p.apply_transformation(TransformationInput(
                ti.app, ti.buffer_control, ti.document, ti.lineno,
                source_to_display, fragments, ti.width, ti.height))
            fragments = transformation.fragments
            display_to_source_functions.append(transformation.display_to_source)
            source_to_display_functions.append(transformation.source_to_display)

        def display_to_source(i):
            for f in reversed(display_to_source_functions):
                i = f(i)
            return i

        # In the case of a nested _MergedProcessor, each processor wants to
        # receive a 'source_to_display' function (as part of the
        # TransformationInput) that has everything in the chain before
        # included, because it can be called as part of the
        # `apply_transformation` function. However, this first
        # `source_to_display` should not be part of the output that we are
        # returning. (This is the most consistent with `display_to_source`.)
        del source_to_display_functions[:1]

        return Transformation(fragments, source_to_display, display_to_source)
