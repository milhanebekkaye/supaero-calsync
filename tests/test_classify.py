import pytest

from calsync.classify import classify, summarize
from calsync.config import ConfigError, parse_config
from calsync.portal import parse_lesson

from .conftest import raw_session


def category_of(config, **kwargs):
    return classify(parse_lesson(raw_session(1, **kwargs)), config).name


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"code": "CM", "unit": "Mécanique du vol"}, "classes"),
        ({"code": "TD", "unit": "Mathématiques"}, "classes"),
        ({"code": "BE", "unit": "Aérodynamique"}, "be"),
        ({"code": "BEN", "unit": "Aérodynamique"}, "critical"),
        ({"code": "BE", "unit": "Structures", "description": "BE noté"}, "critical"),
        ({"code": "BE", "unit": "Structures (BE notée)"}, "critical"),
        ({"code": "EX", "unit": "Thermodynamique"}, "critical"),
        ({"code": "CM", "unit": "Examen de Physique"}, "critical"),
        ({"code": "CM", "unit": "Mécanique", "is_exam": True}, "critical"),
        ({"code": None, "unit": None, "description": "Partiel de maths"}, "critical"),
    ],
)
def test_default_rules(config, kwargs, expected):
    assert category_of(config, **kwargs) == expected


def test_no_false_positive_on_words_containing_be_or_ex(config):
    # 'BE' and 'EX' must match whole words only.
    assert category_of(config, code="CM", unit="Benchmarking des exoplanètes") == "classes"
    assert category_of(config, code="CM", unit="Bernoulli et expérimentation") == "classes"


def test_critical_wins_over_plain_be(config):
    assert category_of(config, code="BE", unit="Aéro", description="BEN") == "critical"


def test_summarize_counts_per_title(config):
    lessons = [
        parse_lesson(raw_session(1, code="BE", unit="Aéro")),
        parse_lesson(raw_session(2, code="BE", unit="Aéro")),
        parse_lesson(raw_session(3, code="CM", unit="Maths")),
    ]
    from calsync.classify import classify_all

    summary = summarize(lessons, classify_all(lessons, config), config)
    assert summary["be"] == {"BE · Aéro": 2}
    assert summary["classes"] == {"CM · Maths": 1}
    assert summary["critical"] == {}


def test_config_validation():
    with pytest.raises(ConfigError, match="missing \\[default\\]"):
        parse_config("[portal]\nurl='x'")
    with pytest.raises(ConfigError, match="regular expression"):
        parse_config('[default]\nname="d"\ncalendar="c"\ncolor_id="1"\n'
                     '[[categories]]\nname="a"\ncalendar="a"\ncolor_id="2"\nrules=["("]')
    with pytest.raises(ConfigError, match="own calendar"):
        parse_config('[default]\nname="d"\ncalendar="same"\ncolor_id="1"\n'
                     '[[categories]]\nname="a"\ncalendar="same"\ncolor_id="2"')
