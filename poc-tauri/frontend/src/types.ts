export interface Incubator {
  id: number;
  name: string;
  temp_mode: string;
}

export interface Tray {
  id: number;
  tray_number: string | null;
  sample_name: string | null;
  incubator_name: string | null;
  weight_lbs: number | null;
  live_count: number | null;
  parasite_level_pct: number | null;
  in_date: string | null;
  out_date: string | null;
  cool_date: string | null;
  cool_days: number | null;
  status: string | null;
}
