from crawlspace.intake import host_variants


def test_produces_four_variants():
    v = host_variants("azayaorganic.com")
    assert v == [
        "https://azayaorganic.com/",
        "https://www.azayaorganic.com/",
        "http://azayaorganic.com/",
        "http://www.azayaorganic.com/",
    ]


def test_www_input_normalises_to_the_same_four():
    assert host_variants("https://www.azayaorganic.com/shop") == host_variants("azayaorganic.com")
