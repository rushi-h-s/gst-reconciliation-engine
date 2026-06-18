export type MatchStatus =
  | "MATCHED"
  | "PROBABLE"
  | "MISMATCH"
  | "BOOKS_ONLY"
  | "TWOB_ONLY"
  | "CORRECTED";

export type DocType = "invoice" | "credit_note" | "debit_note";

export interface PurchaseRegisterEntry {
  id: string;
  supplier_gstin: string;
  supplier_name: string | null;
  inv_no: string;
  inv_date: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  is_rcm: boolean;
  doc_type: DocType;
}

export interface Gstr2bEntry {
  id: string;
  supplier_gstin: string;
  supplier_name: string | null;
  inv_no: string;
  inv_date: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  doc_type: DocType;
}

export interface MatchResult {
  id: string;
  org_id: string;
  client_id: string;
  period: string;
  status: MatchStatus;
  confidence: number | null;
  mismatched_fields: string[] | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  pr_entry: PurchaseRegisterEntry | null;
  gstr2b_entry: Gstr2bEntry | null;
}
