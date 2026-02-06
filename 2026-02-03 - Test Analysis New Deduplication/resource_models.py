from typing import Optional, List, Union, Any
from datetime import datetime, date
from pydantic import BaseModel, Field, model_validator

from serenic_mlkit.utils.codex.enums import (
    PatientGender, PractitionerProfession, ObservationCategory, ObservationValueType,
    ObservationInterpretation, ClinicalImpressionCategory, DiagnosisSeverity,
    DiagnosisClinicalStatus, DiagnosisVerificationStatus, ProcedureStatus,
    ProcedureCategory, ServiceRequestStatus, ServiceRequestCategory,
    DiagnosticReportCategory, MedicationRequestStatus, MedicationDispenseStatus
)

# ---
# Prerequisites Resources

# Location Resource
class LocationModel(BaseModel):
    id: str
    organizational_team_id: str
    name: str
    description: Optional[str] = None
    notes: Optional[str] = None

# Practitioner Resource
class PractitionerModel(BaseModel):
    id: str
    name: str
    profession: PractitionerProfession
    meta: Optional[dict] = {}

# OrganizationalTeam Resource
class OrganizationalTeamModel(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    notes: Optional[str] = None 

# ---

# Structured Field Models

# Concept Structure
class ConceptModel(BaseModel):
    code: Optional[str] = None
    system: Optional[str] = None
    text: str

# Body Site Structure
class BodySiteModel(BaseModel):
    code: Optional[str] = None
    system: Optional[str] = None
    text: str

# Value Reference Structure
class ValueReferenceModel(BaseModel):
    low: Any
    high: Optional[Any] = None
    text: str

# ---

# Resource Models

# Patient Resource
class PatientModel(BaseModel):
    id: str
    birth_date: datetime
    birth_weight: Optional[float] = None
    gender: PatientGender
    meta: Optional[dict] = {}

# Observation Resource
class ObservationModel(BaseModel):
    id: str
    service_request_id: Optional[str] = None
    procedure_id: Optional[str] = None
    category: ObservationCategory
    concept: ConceptModel
    value_type: ObservationValueType
    value: Any
    value_unit: Optional[str] = None
    value_reference: Optional[List[ValueReferenceModel]] = None
    interpretation: Optional[ObservationInterpretation] = None
    body_site: Optional[List[BodySiteModel]] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    effective_datetime: datetime
    issued_datetime: Optional[datetime] = None

    @model_validator(mode='after')
    def validate_value_based_on_type(self):
        """Validate and parse the value field based on the value_type"""
        value_type = self.value_type
        value = self.value
        
        if value_type is None or value is None:
            return self
        
        try:
            if value_type == ObservationValueType.STRING:
                # Convert to string
                self.value = str(value)
                
            elif value_type == ObservationValueType.BOOLEAN:
                # Parse boolean - handle string representations
                if isinstance(value, str):
                    value_lower = value.lower()
                    if value_lower in ('true', '1', 'yes', 'on'):
                        self.value = True
                    elif value_lower in ('false', '0', 'no', 'off'):
                        self.value = False
                    else:
                        raise ValueError(f"Cannot parse '{value}' as boolean")
                else:
                    self.value = bool(value)
                    
            elif value_type == ObservationValueType.INTEGER:
                # Parse as integer
                if isinstance(value, str):
                    self.value = int(value)
                else:
                    self.value = int(value)
                    
            elif value_type == ObservationValueType.FLOAT:
                # Parse as float
                if isinstance(value, str):
                    self.value = float(value)
                else:
                    self.value = float(value)
                    
            elif value_type == ObservationValueType.RANGE:
                # Parse range - expect dict with 'low' and 'high' keys or string like "10-20"
                if isinstance(value, str):
                    if '-' in value:
                        parts = value.split('-', 1)
                        if len(parts) == 2:
                            try:
                                low = float(parts[0].strip())
                                high = float(parts[1].strip())
                                self.value = {'low': low, 'high': high}
                            except ValueError:
                                raise ValueError(f"Cannot parse range '{value}' - invalid number format")
                        else:
                            raise ValueError(f"Invalid range format '{value}' - expected 'low-high'")
                    else:
                        raise ValueError(f"Invalid range format '{value}' - expected 'low-high'")
                elif isinstance(value, dict):
                    if 'low' in value and 'high' in value:
                        self.value = {
                            'low': float(value['low']),
                            'high': float(value['high'])
                        }
                    else:
                        raise ValueError("Range value must have 'low' and 'high' keys")
                else:
                    raise ValueError(f"Range value must be string or dict, got {type(value)}")
                    
            elif value_type == ObservationValueType.RATIO:
                # Parse ratio - expect dict with 'numerator' and 'denominator' or string like "3:4" or "3/4"
                if isinstance(value, str):
                    if ':' or '/' in value:
                        split_char = ':' if ':' in value else '/'
                        parts = value.split(split_char, 1)
                        if len(parts) == 2:
                            try:
                                numerator = float(parts[0].strip())
                                denominator = float(parts[1].strip())
                                if denominator == 0:
                                    raise ValueError("Ratio denominator cannot be zero")
                                self.value = {'numerator': numerator, 'denominator': denominator}
                            except ValueError as e:
                                if "zero" in str(e):
                                    raise e
                                raise ValueError(f"Cannot parse ratio '{value}' - invalid number format")
                        else:
                            raise ValueError(f"Invalid ratio format '{value}' - expected 'numerator:denominator'")
                    else:
                        raise ValueError(f"Invalid ratio format '{value}' - expected 'numerator:denominator'")
                elif isinstance(value, dict):
                    if 'numerator' in value and 'denominator' in value:
                        numerator = float(value['numerator'])
                        denominator = float(value['denominator'])
                        if denominator == 0:
                            raise ValueError("Ratio denominator cannot be zero")
                        self.value = {
                            'numerator': numerator,
                            'denominator': denominator
                        }
                    else:
                        raise ValueError("Ratio value must have 'numerator' and 'denominator' keys")
                else:
                    raise ValueError(f"Ratio value must be string or dict, got {type(value)}")
                    
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid value '{value}' for value_type '{value_type}': {str(e)}")
            
        return self

# ClinicalImpression Resource
class ClinicalImpressionModel(BaseModel):
    id: str
    category: ClinicalImpressionCategory
    text: Optional[str] = None
    subjective: Optional[str] = None
    objective: Optional[str] = None
    assessment: Optional[str] = None
    plan: Optional[str] = None
    performer_id: str | PractitionerModel
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    effective_datetime: datetime

# Diagnosis Resource
class DiagnosisModel(BaseModel):
    id: str
    code: Optional[str] = None
    diagnosis_text: str
    is_primary: bool
    severity: Optional[DiagnosisSeverity] = None
    clinical_status: Optional[DiagnosisClinicalStatus] = None
    verification_status: Optional[DiagnosisVerificationStatus] = None
    # recorder_id: Optional[str] = None
    recorder_id: Optional[str | PractitionerModel] = None
    # asserter_id: Optional[str] = None
    asserter_id: Optional[str | PractitionerModel] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    onset_datetime: Optional[datetime] = None
    recorded_datetime: Optional[datetime] = None
    abatement_datetime: Optional[datetime] = None

# Procedure Resource
class ProcedureModel(BaseModel):
    id: str
    status: Optional[ProcedureStatus] = None
    category: Optional[ProcedureCategory] = None
    code: Optional[str] = None
    procedure_text: str
    body_site: Optional[List[BodySiteModel]] = None
    # performer_id: Optional[str] = None
    performer_id: Optional[str | PractitionerModel] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    performed_datetime: Optional[datetime] = None
    finished_datetime: Optional[datetime] = None

# ServiceRequest Resource
class ServiceRequestModel(BaseModel):
    id: str
    status: ServiceRequestStatus
    category: ServiceRequestCategory
    concept: ConceptModel
    body_site: Optional[List[BodySiteModel]] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    authored_datetime: datetime
    occurrence_datetime: Optional[datetime] = None

# DiagnosticReport Resource
class DiagnosticReportModel(BaseModel):
    id: str
    service_request_id: Optional[str] = None
    category: DiagnosticReportCategory
    concept: ConceptModel
    result: Optional[str] = None
    conclusion: Optional[str] = None
    performer_id: str | PractitionerModel
    # result_interpreter_id: Optional[str] = None
    result_interpreter_id: Optional[str | PractitionerModel] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    effective_datetime: datetime
    issued_datetime: Optional[datetime] = None

# Medication Resource
class MedicationModel(BaseModel):
    id: str
    medication_code: Optional[str] = None
    medication_system: Optional[str] = None
    medication_name: str
    medication_form: Optional[str] = None
    strength_numerator_value: Optional[float] = None
    strength_numerator_unit_code: Optional[str] = None
    strength_denominator_value: Optional[float] = None
    strength_denominator_unit_code: Optional[str] = None
    text: Optional[str] = None
    dosage_quantity: Optional[float] = None
    dosage_unit: Optional[str] = None
    dosage_text: Optional[str] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}

# MedicationRequest Resource
class MedicationRequestModel(BaseModel):
    id: str
    medication_id: str
    status: MedicationRequestStatus
    dosage_frequency: Optional[float] = None
    dosage_period: Optional[float] = None
    dosage_period_unit: Optional[str] = None
    dosage_route: Optional[str] = None
    dosage_quantity: Optional[float] = None
    dosage_text: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    # requester_id: Optional[str] = None
    requester_id: Optional[str | PractitionerModel] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    authored_datetime: datetime

# MedicationDispense Resource
class MedicationDispenseModel(BaseModel):
    id: str
    medication_request_id: Optional[str] = None
    medication_id: str
    status: MedicationDispenseStatus
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    # performer_id: Optional[str] = None
    performer_id: Optional[str | PractitionerModel] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    prepared_datetime: Optional[datetime] = None
    handed_over_datetime: datetime

# MedicationStatement Resource
class MedicationStatementModel(BaseModel):
    id: str
    medication_code: Optional[str] = None
    medication_system: Optional[str] = None
    medication_name: str
    medication_form: Optional[str] = None
    strength_numerator_value: Optional[float] = None
    strength_numerator_unit_code: Optional[str] = None
    strength_denominator_value: Optional[float] = None
    strength_denominator_unit_code: Optional[str] = None
    text: Optional[str] = None
    dosage_frequency: Optional[float] = None
    dosage_period: Optional[float] = None
    dosage_period_unit: Optional[str] = None
    dosage_route: Optional[str] = None
    dosage_quantity: Optional[float] = None
    dosage_text: Optional[str] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    effective_datetime: datetime

# FamilyMemberHistory Resource
class FamilyMemberHistoryModel(BaseModel):
    id: str
    relationship: Optional[str] = None
    text: str
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}

# Billing Resource
class BillingModel(BaseModel):
    id: str
    product_name: str
    product_group: Optional[str] = None
    product_group_code: Optional[str] = None
    total_net_price: float
    currency: Optional[str] = "IDR"
    notes: Optional[str] = None
    extra_data: Optional[dict] = {}
    billable_period_start: datetime
    billable_period_end: Optional[datetime] = None
