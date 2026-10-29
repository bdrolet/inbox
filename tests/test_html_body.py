from services.html_body import text_to_html


def test_paragraphs_become_p_blocks():
    assert text_to_html("Hi Ann,\n\nSounds great.") == "<p>Hi Ann,</p><p>Sounds great.</p>"


def test_single_newline_is_a_line_break():
    assert text_to_html("Thanks,\nBen") == "<p>Thanks,<br>Ben</p>"


def test_markup_in_text_is_escaped():
    assert text_to_html("I&E <draft>") == "<p>I&amp;E &lt;draft&gt;</p>"


def test_blank_lines_with_whitespace_still_split_paragraphs():
    assert text_to_html("one\n  \n\ntwo\r\n\r\nthree") == "<p>one</p><p>two</p><p>three</p>"


def test_surrounding_whitespace_is_dropped():
    assert text_to_html("\n\nhello\n") == "<p>hello</p>"


def test_empty_body_is_empty():
    assert text_to_html("") == ""
