from typing import Optional, List, Dict, Union
from pydantic import BaseModel, model_validator, ValidationError, RootModel

from serenic_mlkit.utils.codex.resource_models import (
    ObservationModel, ClinicalImpressionModel, DiagnosisModel, ProcedureModel,
    ServiceRequestModel, DiagnosticReportModel, MedicationModel,
    MedicationRequestModel, MedicationDispenseModel, MedicationStatementModel,
    FamilyMemberHistoryModel, BillingModel, LocationModel, PractitionerModel,
    OrganizationalTeamModel
)

# ---

# Data Source Models

# Triase IGD
TriaseIgdDirectDataSource = List[ObservationModel]

# CPPT (Catatan Perkembangan Pasien Terintegrasi)
class CpptDataSourceUnit(BaseModel):
    soap: Optional[List[ClinicalImpressionModel]] = []
    observasi: Optional[List[ObservationModel]] = []
    diagnosis: Optional[List[DiagnosisModel]] = []
    prosedur: Optional[List[ProcedureModel]] = []
    obat: Optional[List[MedicationModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        procedure_ids = {proc.id for proc in self.prosedur}
        
        # Check observations for invalid references
        for obs in self.observasi:
            if obs.procedure_id and obs.procedure_id not in procedure_ids:
                raise ValueError(f"Observation {obs.id} references non-existent procedure_id: {obs.procedure_id}")
        
        return self

class CpptDataSource(RootModel[List[CpptDataSourceUnit]]):
    pass

# Asesmen Awal
class AsesmenAwalDataSourceUnit(BaseModel):
    keluhan: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_dahulu: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_sekarang: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_keluarga: Optional[List[FamilyMemberHistoryModel]] = []
    riwayat_penggunaan_obat: Optional[List[MedicationStatementModel]] = []
    pemeriksaan_fisik: Optional[List[ObservationModel]] = []
    hasil_lab: Optional[List[ObservationModel]] = []
    laporan_radiologi: Optional[List[DiagnosticReportModel]] = []
    laporan_lab: Optional[List[DiagnosticReportModel]] = []
    diagnosis: Optional[List[DiagnosisModel]] = []
    prosedur: Optional[List[ProcedureModel]] = []
    assessment_text: Optional[List[ClinicalImpressionModel]] = []

class AsesmenAwalDataSource(RootModel[List[AsesmenAwalDataSourceUnit]]):
    pass

# Resume Medis
class ResumeMedisDataSourceUnit(BaseModel):
    keluhan: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_dahulu: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_sekarang: Optional[List[ClinicalImpressionModel]] = []
    riwayat_penyakit_keluarga: Optional[List[FamilyMemberHistoryModel]] = []
    riwayat_penggunaan_obat: Optional[List[MedicationStatementModel]] = []
    pemeriksaan_fisik: Optional[List[ObservationModel]] = []
    hasil_lab: Optional[List[ObservationModel]] = []
    laporan_radiologi: Optional[List[DiagnosticReportModel]] = []
    laporan_lab: Optional[List[DiagnosticReportModel]] = []
    diagnosis: Optional[List[DiagnosisModel]] = []
    prosedur: Optional[List[ProcedureModel]] = []
    obat: Optional[List[MedicationModel]] = []
    resume_text: Optional[List[ClinicalImpressionModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        procedure_ids = {proc.id for proc in self.prosedur}
        
        # Check observations for invalid references
        for obs in (self.pemeriksaan_fisik + self.hasil_lab):
            if obs.procedure_id and obs.procedure_id not in procedure_ids:
                raise ValueError(f"Observation {obs.id} references non-existent procedure_id: {obs.procedure_id}")
        
        return self

class ResumeMedisDataSource(RootModel[List[ResumeMedisDataSourceUnit]]):
    pass

# Penunjang Lab
class PenunjangLabDataSourceUnit(BaseModel):
    pemesanan: Optional[List[ServiceRequestModel]] = []
    hasil_lab: Optional[List[ObservationModel]] = []
    laporan_lab: Optional[List[DiagnosticReportModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        service_request_ids = {sr.id for sr in self.pemesanan}
        
        # Check observations for invalid references
        for obs in self.hasil_lab:
            if obs.service_request_id and obs.service_request_id not in service_request_ids:
                raise ValueError(f"Observation {obs.id} references non-existent service_request_id: {obs.service_request_id}")
        
        # Check diagnostic reports for invalid references
        for report in self.laporan_lab:
            if report.service_request_id and report.service_request_id not in service_request_ids:
                raise ValueError(f"DiagnosticReport {report.id} references non-existent service_request_id: {report.service_request_id}")
        
        return self

class PenunjangLabDataSource(RootModel[List[PenunjangLabDataSourceUnit]]):
    pass

# Penunjang Radiologi
class PenunjangRadiologiDataSourceUnit(BaseModel):
    pemesanan: Optional[List[ServiceRequestModel]] = []
    laporan_radiologi: Optional[List[DiagnosticReportModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        service_request_ids = {sr.id for sr in self.pemesanan}
        
        # Check diagnostic reports for invalid references
        for report in self.laporan_radiologi:
            if report.service_request_id and report.service_request_id not in service_request_ids:
                raise ValueError(f"DiagnosticReport {report.id} references non-existent service_request_id: {report.service_request_id}")
        
        return self

class PenunjangRadiologiDataSource(RootModel[List[PenunjangRadiologiDataSourceUnit]]):
    pass


# Laporan Operasi
class LaporanOperasiDataSourceUnit(BaseModel):
    pemesanan: Optional[List[ServiceRequestModel]] = []
    diagnosis_pra_bedah: Optional[List[DiagnosisModel]] = []
    diagnosis_pasca_bedah: Optional[List[DiagnosisModel]] = []
    prosedur_operasi: Optional[List[ProcedureModel]] = []
    laporan_operasi: Optional[List[DiagnosticReportModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        service_request_ids = {sr.id for sr in self.pemesanan}
        
        # Check diagnostic reports for invalid service request references
        for report in self.laporan_operasi:
            if report.service_request_id and report.service_request_id not in service_request_ids:
                raise ValueError(f"DiagnosticReport {report.id} references non-existent service_request_id: {report.service_request_id}")
        
        return self

class LaporanOperasiDataSource(RootModel[List[LaporanOperasiDataSourceUnit]]):
    pass


# Tindakan Medis Lain
class ProsedurMedisLainDataSourceUnit(BaseModel):
    pemesanan: Optional[List[ServiceRequestModel]] = []
    hasil_prosedur: Optional[List[ObservationModel]] = []
    laporan_prosedur: Optional[List[DiagnosticReportModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        service_request_ids = {sr.id for sr in self.pemesanan}
        
        # Check observations for invalid references
        for obs in self.hasil_prosedur:
            if obs.service_request_id and obs.service_request_id not in service_request_ids:
                raise ValueError(f"Observation {obs.id} references non-existent service_request_id: {obs.service_request_id}")
        
        # Check diagnostic reports for invalid references
        for report in self.laporan_prosedur:
            if report.service_request_id and report.service_request_id not in service_request_ids:
                raise ValueError(f"DiagnosticReport {report.id} references non-existent service_request_id: {report.service_request_id}")
        
        return self

class ProsedurMedisLainDataSource(RootModel[List[ProsedurMedisLainDataSourceUnit]]):
    pass
 

# Diagnosis Aktif
DiagnosisAktifDataSource = List[DiagnosisModel]


# Obat
class ObatDataSourceUnit(BaseModel):
    obat: Optional[List[MedicationModel]] = []
    resep_obat: Optional[List[MedicationRequestModel]] = []
    pengeluaran_obat: Optional[List[MedicationDispenseModel]] = []
    
    @model_validator(mode='after')
    def validate_internal_references(self):
        """Validate that internal references exist within the same data source"""
        # Collect all IDs from each section
        medication_ids = {med.id for med in self.obat}
        medication_request_ids = {mr.id for mr in self.resep_obat}
        
        # Check medication requests for invalid medication references
        for med_req in self.resep_obat:
            if med_req.medication_id and med_req.medication_id not in medication_ids:
                raise ValueError(f"MedicationRequest {med_req.id} references non-existent medication_id: {med_req.medication_id}")
        
        # Check medication dispenses for invalid references
        for med_disp in self.pengeluaran_obat:
            if med_disp.medication_id and med_disp.medication_id not in medication_ids:
                raise ValueError(f"MedicationDispense {med_disp.id} references non-existent medication_id: {med_disp.medication_id}")
            if med_disp.medication_request_id and med_disp.medication_request_id not in medication_request_ids:
                raise ValueError(f"MedicationDispense {med_disp.id} references non-existent medication_request_id: {med_disp.medication_request_id}")
        
        return self

class ObatDataSource(RootModel[List[ObatDataSourceUnit]]):
    pass


# Billing
BillingDataSource = List[BillingModel]


# Complete Data Sources Model
class EncounterDataSourcesModel(BaseModel):
    triase_igd: Optional[TriaseIgdDirectDataSource] = None
    cppt: Optional[CpptDataSource] = None
    asesmen_awal: Optional[AsesmenAwalDataSource] = None
    resume_medis: Optional[ResumeMedisDataSource] = None
    penunjang_lab: Optional[PenunjangLabDataSource] = None
    penunjang_radiologi: Optional[PenunjangRadiologiDataSource] = None
    laporan_operasi: Optional[LaporanOperasiDataSource] = None
    prosedur_medis_lain: Optional[ProsedurMedisLainDataSource] = None
    diagnosis_aktif: Optional[DiagnosisAktifDataSource] = None
    obat: Optional[ObatDataSource] = None
    billing: Optional[BillingDataSource] = None

# ---

# Prerequisites Model

class PrerequisitesDataSource(BaseModel):
    lokasi: Optional[List[LocationModel]] = []
    praktisi: Optional[List[PractitionerModel]] = []
    tim_organisasi: Optional[List[OrganizationalTeamModel]] = [] 