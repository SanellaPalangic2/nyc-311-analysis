"""Smoke tests for the dashboard, using Streamlit's built-in AppTest
(runs the app script without a browser).

Run (from the project root, with the app's packages installed):
    pip install -r app/requirements.txt pytest
    pytest app/test_app.py
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent / "streamlit_app.py")
PAGES = ["Overview", "Resolution time explorer", "Survival curves", "Prediction model"]
# The explorer's measure picker shows these labels.
MEASURE_LABELS = {"median_hours": "Median hours to close", "p90_hours": "90th percentile hours",
                  "pct_closed_within_30d": "% closed within 30 days",
                  "pct_no_close_date": "% with no close date"}


def open_page(page):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="page").set_value(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_without_errors(page):
    at = open_page(page)
    assert not at.exception, at.exception
    assert at.title[0].value  # each page has a title


def test_overview_metrics_come_from_the_data_files():
    at = open_page("Overview")
    values = {m.label: m.value for m in at.metric}
    assert values["Requests"] == "3,655,042"
    assert values["Median time to close"] == "6.61 h"
    assert values["Median incl. open requests"] == "7.12 h"


@pytest.mark.parametrize("group", ["agency", "borough", "season"])
@pytest.mark.parametrize("measure", ["median_hours", "p90_hours", "pct_closed_within_30d",
                                     "pct_no_close_date"])
def test_explorer_every_grouping_and_measure(group, measure):
    at = open_page("Resolution time explorer")
    at.radio(key="group").set_value(group.title())
    at.selectbox(key="measure").set_value(MEASURE_LABELS[measure])
    at.run()
    assert not at.exception, at.exception
    assert at.radio(key="group").value == group.title()
    assert at.selectbox(key="measure").value == MEASURE_LABELS[measure]
    table = at.dataframe[0].value
    assert group.title() in table.columns
    expected = {"agency": 15, "borough": 5, "season": 4}[group]
    assert len(table) == expected  # every group shown (unknown borough excluded)


def test_explorer_with_all_filters_set():
    at = open_page("Resolution time explorer")
    at.selectbox(key="borough").set_value("Bronx")
    at.selectbox(key="season").set_value("Summer")
    at.selectbox(key="agency").set_value("NYPD")
    at.run()
    assert not at.exception, at.exception
    chosen = [at.selectbox(key=k).value for k in ("borough", "season", "agency")]
    assert chosen == ["Bronx", "Summer", "NYPD"]
    # Grouped by agency with the agency filter set: only NYPD is shown, with a note
    assert at.dataframe[0].value["Agency"].tolist() == ["NYPD"]
    assert at.info


def test_survival_page_with_bands_and_one_agency():
    at = open_page("Survival curves")
    at.checkbox(key="km_ci").check()
    at.multiselect(key="km_agencies").set_value(["All agencies"])
    at.run()
    assert not at.exception, at.exception
    assert at.dataframe[0].value["Agency"].tolist() == ["All agencies"]


def test_survival_page_with_nothing_selected_shows_warning():
    at = open_page("Survival curves")
    at.multiselect(key="km_agencies").set_value([])
    at.run()
    assert not at.exception, at.exception
    assert at.warning


def test_model_page_confusion_matrix_switches_model():
    at = open_page("Prediction model")
    at.selectbox(key="cm_model").set_value("Logistic regression")
    at.run()
    assert not at.exception, at.exception
    assert at.selectbox(key="cm_model").value == "Logistic regression"
