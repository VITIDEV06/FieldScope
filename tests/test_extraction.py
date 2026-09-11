import copy
import pytest

from ai import sanitize_extraction, _local_extract
from normalization import normalize_modality, equipment_mentions


@pytest.mark.parametrize("alias, canonical", [
    ("MRI", "MR"), ("Resonancia magnética", "MR"), ("Resonancia", "MR"),
    ("CAT", "CT"), ("Tomografía", "CT"), ("Tomógrafo", "CT"),
    ("Ultrasonido", "Ultrasound"), ("Ecografía", "Ultrasound"),
    ("Rayos X", "X-Ray"), ("Radiografía", "X-Ray"),
    ("Mamografía", "Mammography"), ("Mamógrafo", "Mammography"),
    ("PET", "PET"), ("PET/CT", "PET/CT"), ("PET CT", "PET/CT"),
    ("Arco en C", "C-Arm"), ("Fluoroscopia", "Fluoroscopy"),
    ("Monitor de paciente", "Patient Monitor"), ("Ventilador", "Ventilator"),
    ("Electrocardiógrafo", "ECG"), ("Desfibrilador", "Defibrillator"),
])
def test_modalities(alias, canonical):
    assert normalize_modality(alias) == canonical
    hits = equipment_mentions("dos equipos de " + alias)
    assert len(hits) == 1
    assert hits[0]["modality"] == canonical
    assert hits[0]["quantity"] == 2


@pytest.mark.parametrize("age", ["8", "ocho"])
def test_equipment_field_association(age):
    text = f"dos MR Siemens de {age} años y un CT Philips"
    hallucinated = {"customer_name": "Hospital Inventado", "city": "Madrid", "country": "España", "equipment": [
        {"modality": "MR", "quantity": 99, "manufacturer": "Siemens", "model": "Siemens", "estimated_age": 8},
        {"modality": "CT", "quantity": 22, "manufacturer": "Philips", "model": "Incisive", "estimated_age": 8, "serial_number": "FAKE-123"}]}
    original = copy.deepcopy(hallucinated)
    result = sanitize_extraction(hallucinated, text)
    mr, ct = result["equipment"]
    assert (mr["quantity"], mr["manufacturer"], mr["estimated_age"], mr["model"]) == (2, "Siemens", 8, None)
    assert (ct["quantity"], ct["manufacturer"], ct["estimated_age"], ct["model"], ct["serial_number"]) == (1, "Philips", None, None, None)
    assert result["customer_name"] == "Desconocido"
    assert result["city"] is None and result["country"] is None
    assert hallucinated == original


@pytest.mark.parametrize("text,model", [("un CT Philips", None), ("un CT Philips Incisive", "Incisive")])
def test_explicit_model_only(text, model):
    result = sanitize_extraction({"equipment": [{"modality": "CT", "manufacturer": "Philips", "model": "Incisive"}]}, text)
    assert result["equipment"][0]["model"] == model


def test_wrong_modality_repaired():
    result = sanitize_extraction({"equipment": [{"modality": "X-Ray"}]}, "un tomógrafo Philips")
    assert result["equipment"][0]["modality"] == "CT"


def test_no_cross_row_manufacturer_or_model():
    result = sanitize_extraction({"equipment": [
        {"modality": "MR", "manufacturer": "Philips", "model": "Incisive"},
        {"modality": "CT", "manufacturer": "Philips", "model": "Incisive"},
    ]}, "un MR Siemens y un CT Philips Incisive")
    assert result["equipment"][0]["manufacturer"] is None
    assert result["equipment"][0]["model"] is None
    assert result["equipment"][1]["model"] == "Incisive"


def test_unknown_quantity_and_age_range():
    eq = sanitize_extraction({"equipment": [{"modality": "MR", "quantity": 5, "estimated_age": 9}]}, "MR de 8–10 años")["equipment"][0]
    assert eq["quantity"] is None and eq["estimated_age"] is None


def test_distinct_same_modality():
    eq = _local_extract("un CT Philips de 8 años y un CT Siemens de 2 años")["equipment"]
    assert len(eq) == 2
    assert [(e["manufacturer"], e["estimated_age"]) for e in eq] == [("Philips", 8), ("Siemens", 2)]


def test_missing_fields():
    result = _local_extract("Hay tres MR.")
    assert result["customer_name"] == "Desconocido"
    assert result["equipment"][0]["quantity"] == 3
    assert result["equipment"][0]["manufacturer"] is None
    assert _local_extract("Visité Hospital Demo.")["equipment"] == []


def test_context_field_isolation():
    context = {"customer_name": "Hospital Demo", "_question_key": "equipment.1.model", "equipment": [
        {"modality": "MR", "manufacturer": "Siemens", "model": "DemoMag", "estimated_age": 8, "quantity": 2},
        {"modality": "CT", "manufacturer": "Philips", "model": None, "estimated_age": None, "quantity": 1}]}
    result = _local_extract("Incisive", context)
    assert result["equipment"][1]["model"] == "Incisive"
    assert result["equipment"][1]["estimated_age"] is None
    assert result["equipment"][0]["model"] == "DemoMag"
