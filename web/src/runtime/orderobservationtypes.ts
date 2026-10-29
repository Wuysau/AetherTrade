export type ArchiveRecord = {record_id:string;sequence?:number;payload:Record<string,unknown>;canonical_payload_hash?:string};
export type ProgramView = {
  program_id:string;scope_id:string;scope?:Record<string,string>;contract_version:string;codec_status:string;canonical_payload_hash:string;
  template:string|null;execution_mode:string|null;execution_state:string;state_origin?:string;protection_status:string;
  children_complete:string|null;execution_authorized:false;payload:Record<string,unknown>;
  nodes:{node_id:string;role:string;child_origin:string;order:Record<string,unknown>}[];
  intakes?:ArchiveRecord[];records?:Record<string,ArchiveRecord[]>;state_observation?:Record<string,unknown>|null;
  capability?:{archived_snapshot:Record<string,unknown>|null;current_qualification:string;admission_blockers:string[]};
  rejection_reasons?:{code:string;stage:string}[];truncated?:Record<string,boolean>;
};
export type ProgramPage = {items:ProgramView[];scopes:{scope_id:string;scope:Record<string,string>}[];next_cursor:string|null;observation_status:string};
export type CapabilityView = {features:{id:string;product:string;feature:string;target_required:boolean;compilation_status:string;engineering_test_status:string;venue_qualification:string}[];current_qualification:string;admission_blockers:string[];expression_versions:Record<string,string[]>;as_of?:string};
