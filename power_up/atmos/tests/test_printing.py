"""Ticket layout, ESC/POS serialization, and transports."""

from datetime import datetime
from decimal import Decimal

import pytest

from power_up.atmos.printing.art import (
    BEER_STEIN,
    COCKTAIL_COUPE,
    OLD_FASHIONED,
    select_ascii_art,
    select_mission,
)
from power_up.atmos.printing.escpos import (
    CODEPAGE_CP858,
    INIT,
    encode_text,
    encode_ticket,
    render_plain_text,
)
from power_up.atmos.printing.layout import (
    Paper,
    QrCode,
    Text,
    TicketData,
    TicketLine,
    justify,
    money,
    render_ticket,
    wrap,
)
from power_up.atmos.printing.transport import (
    FileTransport,
    NullTransport,
    Tcp9100Transport,
    TransportError,
)

AT = datetime(2026, 8, 16, 21, 47)


def ticket(**overrides):
    defaults = dict(
        venue_name="The Velvet Hour",
        table_label="12",
        ticket_code="T12-04",
        placed_at=AT,
        persona="The Whispering Gambler",
        lines=(
            TicketLine("Old Fashioned", 2, Decimal("8.50"), note="no ice"),
            TicketLine("Smoky Mezcalita", 1, Decimal("9.50")),
        ),
        vignette="The rain kept its own counsel.\nNobody wrote it down.",
        qr_payload="https://power-up.lu/atmos/t/abc123",
        contains_alcohol=True,
    )
    defaults.update(overrides)
    return TicketData(**defaults)


class TestHelpers:
    def test_justify_puts_the_price_flush_right(self):
        assert justify("TOTAL", "26.50", 20) == "TOTAL          26.50"

    def test_justify_truncates_the_label_not_the_price(self):
        result = justify("A very long drink name indeed", "999.99", 20)
        assert result.endswith("999.99")
        assert len(result) == 20

    def test_justify_survives_a_price_wider_than_the_paper(self):
        assert len(justify("X", "1" * 40, 20)) == 20

    def test_justify_keeps_the_cents_when_it_must_truncate(self):
        # Clipping the tail would drop the cents, which is the failure the
        # left-truncation exists to prevent in the first place.
        assert justify("X", "123456.78", 6).endswith("56.78")

    def test_wrap_does_not_double_subtract_the_indent(self):
        # With the double-subtraction bug the usable width was 28, so no line
        # could exceed it. Indented text was silently losing 4 columns of paper.
        lines = wrap("aaa " * 20, 32, indent="    ")
        assert max(len(line) for line in lines) > 28
        assert all(len(line) <= 32 for line in lines)

    def test_unknown_currency_still_labels_the_number(self):
        assert money(Decimal("10"), "CHF") == "CHF 10.00"

    def test_long_venue_names_wrap_instead_of_overflowing(self):
        long_name = ticket(venue_name="The Extremely Long Speakeasy Name Society")
        text = render_plain_text(render_ticket(long_name, Paper.MM58), Paper.MM58)
        assert all(len(line) <= 32 for line in text.splitlines())

    def test_wrap_respects_the_column_count(self):
        assert all(len(line) <= 24 for line in wrap("word " * 30, 24))

    def test_wrap_indents_continuations(self):
        lines = wrap("word " * 20, 24, indent="    ")
        assert all(line.startswith("    ") for line in lines)

    def test_money_uses_the_euro_sign(self):
        assert money(Decimal("8.5")) == "€8.50"

    def test_money_rounds_half_up(self):
        assert money(Decimal("8.005")) == "€8.01"


class TestTicketData:
    def test_total_is_the_sum_of_line_totals(self):
        assert ticket().total == Decimal("26.50")

    def test_line_total_multiplies_by_quantity(self):
        assert TicketLine("X", 3, Decimal("2.35")).line_total == Decimal("7.05")


class TestLayout:
    def test_every_line_fits_the_paper(self):
        for paper in (Paper.MM80, Paper.MM58):
            text = render_plain_text(render_ticket(ticket(), paper), paper)
            assert all(len(line) <= paper.columns for line in text.splitlines())

    def test_narrow_paper_still_shows_price_and_total(self):
        text = render_plain_text(render_ticket(ticket(), Paper.MM58), Paper.MM58)
        assert "€17.00" in text
        assert "€26.50" in text

    def test_shows_persona_prominently(self):
        text = render_plain_text(render_ticket(ticket()))
        assert "THE WHISPERING GAMBLER" in text

    def test_includes_the_vignette(self):
        text = render_plain_text(render_ticket(ticket()))
        assert "The rain kept its own counsel." in text

    def test_renders_ascii_art_when_present(self):
        art_ticket = ticket(ascii_art=OLD_FASHIONED)
        for paper in (Paper.MM80, Paper.MM58):
            text = render_plain_text(render_ticket(art_ticket, paper), paper)
            assert ".-------." in text
            assert all(len(line) <= paper.columns for line in text.splitlines())

    def test_renders_mission_when_present(self):
        mission_ticket = ticket(mission="Covert Toast: Raise your glass to table 4.")
        for paper in (Paper.MM80, Paper.MM58):
            text = render_plain_text(render_ticket(mission_ticket, paper), paper)
            assert "SPEAKEASY MISSION" in text
            assert "Covert Toast" in text
            assert all(len(line) <= paper.columns for line in text.splitlines())

    def test_item_notes_are_indented_under_the_item(self):
        text = render_plain_text(render_ticket(ticket()))
        assert "    no ice" in text

    def test_alcohol_prompts_a_check_at_the_table(self):
        assert "CHECK AT TABLE" in render_plain_text(render_ticket(ticket()))

    def test_no_alcohol_notice_when_the_round_is_soft(self):
        soft = ticket(contains_alcohol=False)
        assert "CHECK AT TABLE" not in render_plain_text(render_ticket(soft))

    def test_ends_with_a_cut(self):
        assert render_plain_text(render_ticket(ticket())).endswith(">8")

    def test_survives_an_empty_order(self):
        assert render_plain_text(render_ticket(ticket(lines=())))


class TestArtAndMissions:
    def test_select_ascii_art_matches_drink_types(self):
        assert select_ascii_art(["Old Fashioned"]) == OLD_FASHIONED
        assert select_ascii_art(["Pilsner, 33cl"]) == BEER_STEIN
        assert select_ascii_art(["French 75"]) == COCKTAIL_COUPE

    def test_select_mission_returns_deterministic_string(self):
        m1 = select_mission("test-order-1")
        m2 = select_mission("test-order-1")
        assert m1 == m2
        assert len(m1) > 10


class TestEscPos:
    def test_starts_with_init_and_codepage(self):
        payload = encode_ticket(render_ticket(ticket()))
        assert payload.startswith(INIT + CODEPAGE_CP858)

    def test_euro_sign_encodes_to_the_cp858_slot(self):
        # The whole reason for CP858. In the default CP437 this is a wrong glyph
        # on every ticket, and it is always found on install night.
        assert encode_text("€") == b"\xd5"

    def test_transliterates_rather_than_raising(self):
        assert encode_text("Michał") == b"Michal"

    def test_never_raises_on_unmappable_input(self):
        assert encode_text("日本語")  # replacement chars, but bytes come out

    def test_bold_is_switched_on_and_off_again(self):
        payload = encode_ticket([Text("LOUD", bold=True)])
        assert b"\x1bE\x01" in payload
        assert b"\x1bE\x00" in payload

    def test_alignment_is_reset_after_each_line(self):
        from power_up.atmos.printing.layout import Align

        payload = encode_ticket([Text("mid", Align.CENTER)])
        assert payload.endswith(b"\x1ba\x00")

    def test_partial_cut_command(self):
        assert encode_ticket(render_ticket(ticket())).endswith(b"\x1dVB\x00")

    def test_qr_store_length_matches_the_payload(self):
        data = "https://example.test/x"
        payload = encode_ticket([QrCode(data)])
        expected_len = len(data) + 3
        assert bytes([expected_len & 0xFF, (expected_len >> 8) & 0xFF, 49, 80, 48]) in payload

    def test_qr_length_is_two_bytes_for_long_payloads(self):
        long_data = "x" * 400
        payload = encode_ticket([QrCode(long_data)])
        expected = 403
        assert bytes([expected & 0xFF, (expected >> 8) & 0xFF, 49, 80, 48]) in payload

    def test_double_height_size_byte(self):
        assert b"\x1d!\x01" in encode_ticket([Text("BIG", double_height=True)])

    def test_double_width_and_height_size_byte(self):
        payload = encode_ticket([Text("BIG", double_width=True, double_height=True)])
        assert b"\x1d!\x11" in payload


class TestControlByteInjection:
    """Menu text does not pass through `lore.safety`, so it is guarded here."""

    def test_escape_bytes_in_a_drink_name_cannot_reach_the_printer(self):
        # `\x1d V A \x00` is a cut command. A drink called this would otherwise
        # sever the ticket mid-order.
        payload = encode_ticket(
            render_ticket(ticket(lines=(TicketLine("Mojito\x1dVA\x00", 1, Decimal("7.00")),)))
        )
        assert payload.count(b"\x1dVA\x00") == 0

    def test_escape_bytes_in_a_note_are_stripped(self):
        payload = encode_text("no ice\x1bt\x00")
        assert b"\x1b" not in payload

    def test_only_the_intended_cut_survives(self):
        evil = TicketLine("Rye\x1dVB\x00", 1, Decimal("9.00"))
        payload = encode_ticket(render_ticket(ticket(lines=(evil,))))
        assert payload.count(b"\x1dVB\x00") == 1


class TestBarcode:
    def test_rejects_payloads_too_long_for_a_single_length_byte(self):
        from power_up.atmos.printing.escpos import BarcodeTooLong
        from power_up.atmos.printing.layout import Barcode

        with pytest.raises(BarcodeTooLong):
            encode_ticket([Barcode("A" * 300)])

    def test_non_ascii_is_replaced_rather_than_mis_encoded(self):
        from power_up.atmos.printing.layout import Barcode

        assert b"{BT12-?4" in encode_ticket([Barcode("T12-€4")])


class TestTransports:
    def test_null_transport_records_jobs(self):
        transport = NullTransport()
        transport.send(b"hello")
        assert transport.sent == [b"hello"]

    def test_file_transport_appends(self, tmp_path):
        target = tmp_path / "spool" / "out.bin"
        transport = FileTransport(target)
        transport.send(b"one")
        transport.send(b"two")
        assert target.read_bytes() == b"onetwo"

    def test_file_transport_reports_failure(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        with pytest.raises(TransportError):
            FileTransport(blocker / "out.bin").send(b"x")

    def test_tcp_transport_reports_unreachable_printers(self):
        # Port 9 (discard) is closed on the loopback in CI containers.
        with pytest.raises(TransportError) as exc:
            Tcp9100Transport("127.0.0.1", port=9, timeout=0.25).send(b"x")
        assert "unreachable" in str(exc.value)
