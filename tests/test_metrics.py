from yta.metrics import BY_NAME, METRICS, map_header, unmapped_name

# Header lines of real German Studio exports (Datum and Videos tables), 2026-10.
REAL_HEADERS = [
    "Datum",
    "Videos",
    "Videotitel",
    "Veröffentlichungszeitpunkt des Videos",
    "Dauer",
    "Gelegentliche Zuschauer",
    "Regelmäßige Zuschauer",
    "Engaged Views",
    "Abonnenten",
    "Durchschnittliche Wiedergabedauer in Prozent (%)",
    "Hinzugefügte Videos",
    "Veröffentlichte Videos",
    "Thumbnail-Impressionen",
    "Thumbnail-Klickrate (%)",
    "Bis zum Ende angesehen (%)",
    "Einzelne Zuschauer",
    "Unique Reach",
    "Aufrufe (Co-Viewing)",
    "Aufrufe pro Zuschauer (Durchschnitt)",
    "Neue Zuschauer",
    "Wiederkehrende Zuschauer",
    "Hypes",
    "Hype-Punkte",
    "Gewonnene Abonnenten",
    "„Mag ich nicht“-Bewertungen",
    "Likes",
    "Verlorene Abonnenten",
    "Likes und Dislikes im Vergleich (%)",
    "Geteilte Inhalte",
    "Kommentare hinzugefügt",
    "Aufrufe",
    "Wiedergabezeit (Stunden)",
    "Durchschnittliche Wiedergabedauer",
]


def test_every_real_header_is_mapped():
    unmapped = [h for h in REAL_HEADERS if map_header(h).kind == "unmapped"]
    assert unmapped == []


def test_english_headers_map_to_same_metrics():
    assert map_header("Views").name == "views"
    assert map_header("Impressions click-through rate (%)").name == "impressions_ctr"
    assert map_header("Date").kind == "dimension"


def test_unknown_header_is_kept_with_normalized_name():
    mapping = map_header("Völlig neue Kennzahl (%)")
    assert mapping.kind == "unmapped"
    assert mapping.name == unmapped_name("Völlig neue Kennzahl (%)") == "x_vollig_neue_kennzahl"


def test_registry_is_consistent():
    names = [m.name for m in METRICS]
    assert len(names) == len(set(names))
    for m in METRICS:
        if m.aggregation == "ratio":
            assert m.ratio is not None
        if m.aggregation == "weighted":
            assert m.weight in BY_NAME
        if m.additive:
            assert m.aggregation == "sum"


def test_distinct_viewer_metrics_are_not_additive():
    for name in (
        "unique_viewers",
        "new_viewers",
        "returning_viewers",
        "casual_viewers",
        "regular_viewers",
        "unique_reach",
        "views_per_viewer",
    ):
        assert BY_NAME[name].aggregation == "period_total"
        assert not BY_NAME[name].additive
