-- ============================================================
-- Model 3 — VMS Federation Seed Data
-- ============================================================
-- Run AFTER:
--   shared/db/schema.sql   (creates departments, vehicles_watchlist)
--   shared/db/seed.sql     (inserts departments rows we FK into)
--   federation_schema.sql  (creates the 5 new tables)
--
-- Uses INSERT … ON CONFLICT DO NOTHING so re-runs are safe.
-- The UUIDs are hardcoded so cross-table FKs work predictably.
-- ============================================================

-- ── Hardcoded stable UUIDs ──────────────────────────────────
-- These are referenced from federation_cameras & federation_events below.

DO $$
DECLARE
    -- Federated system UUIDs
    sys_police   UUID := 'a1000001-0000-0000-0000-000000000001';
    sys_rto      UUID := 'a1000002-0000-0000-0000-000000000002';
    sys_muni     UUID := 'a1000003-0000-0000-0000-000000000003';

    -- Department UUIDs (must match rows in seed.sql — fetched dynamically)
    dept_police  UUID;
    dept_rto     UUID;
    dept_muni    UUID;

    -- Federated camera UUIDs (Police)
    cam_p1 UUID := 'b1000001-0000-0000-0000-000000000001';
    cam_p2 UUID := 'b1000002-0000-0000-0000-000000000002';
    cam_p3 UUID := 'b1000003-0000-0000-0000-000000000003';
    cam_p4 UUID := 'b1000004-0000-0000-0000-000000000004';
    cam_p5 UUID := 'b1000005-0000-0000-0000-000000000005';

    -- Federated camera UUIDs (RTO)
    cam_r1 UUID := 'b2000001-0000-0000-0000-000000000001';
    cam_r2 UUID := 'b2000002-0000-0000-0000-000000000002';
    cam_r3 UUID := 'b2000003-0000-0000-0000-000000000003';
    cam_r4 UUID := 'b2000004-0000-0000-0000-000000000004';

    -- Federated camera UUIDs (Municipal)
    cam_m1 UUID := 'b3000001-0000-0000-0000-000000000001';
    cam_m2 UUID := 'b3000002-0000-0000-0000-000000000002';
    cam_m3 UUID := 'b3000003-0000-0000-0000-000000000003';
    cam_m4 UUID := 'b3000004-0000-0000-0000-000000000004';

    -- Federated event UUIDs (for seed events)
    ev1 UUID := 'c1000001-0000-0000-0000-000000000001';
    ev2 UUID := 'c1000002-0000-0000-0000-000000000002';
    ev3 UUID := 'c1000003-0000-0000-0000-000000000003';
    ev4 UUID := 'c1000004-0000-0000-0000-000000000004';
    ev5 UUID := 'c1000005-0000-0000-0000-000000000005';
    ev6 UUID := 'c1000006-0000-0000-0000-000000000006';
    ev7 UUID := 'c1000007-0000-0000-0000-000000000007';
    ev8 UUID := 'c1000008-0000-0000-0000-000000000008';
    ev9 UUID := 'c1000009-0000-0000-0000-000000000009';
    ev10 UUID := 'c1000010-0000-0000-0000-000000000010';

BEGIN
    -- Resolve department IDs dynamically (names must match seed.sql)
    SELECT id INTO dept_police FROM departments WHERE name ILIKE '%police%' OR name ILIKE '%home%' LIMIT 1;
    SELECT id INTO dept_rto    FROM departments WHERE name ILIKE '%transport%' OR name ILIKE '%rto%' LIMIT 1;
    SELECT id INTO dept_muni   FROM departments WHERE name ILIKE '%municipal%' OR name ILIKE '%corporation%' LIMIT 1;

    -- Fall back to any department if names don't match exactly
    IF dept_police IS NULL THEN SELECT id INTO dept_police FROM departments LIMIT 1; END IF;
    IF dept_rto    IS NULL THEN SELECT id INTO dept_rto    FROM departments OFFSET 1 LIMIT 1; END IF;
    IF dept_muni   IS NULL THEN SELECT id INTO dept_muni   FROM departments OFFSET 2 LIMIT 1; END IF;

    -- ── 1. Federated Systems ─────────────────────────────────

    INSERT INTO federated_systems (id, name, vendor, department_id, protocol, status, camera_count, last_heartbeat)
    VALUES
        (sys_police, 'Gujarat Police VMS (Milestone)', 'Milestone', dept_police, 'simulated', 'connected', 5, now()),
        (sys_rto,    'Gujarat RTO Checkpoint System (HikCentral)', 'Hikvision', dept_rto, 'simulated', 'connected', 4, now()),
        (sys_muni,   'AMC City Surveillance (Dahua)', 'Dahua', dept_muni, 'simulated', 'connected', 4, now())
    ON CONFLICT (id) DO NOTHING;

    -- ── 2. Federated Cameras — Police (5 cameras) ────────────
    -- Real Gujarat GPS coordinates

    INSERT INTO federated_cameras (id, system_id, external_id, name, location, location_label, department_id)
    VALUES
        (cam_p1, sys_police, 'cam-pol-01', 'Ahmedabad Police HQ', ST_SetSRID(ST_MakePoint(72.5714, 23.0225), 4326)::GEOGRAPHY, 'Shahibaug, Ahmedabad', dept_police),
        (cam_p2, sys_police, 'cam-pol-02', 'Surat Control Room',  ST_SetSRID(ST_MakePoint(72.8311, 21.1702), 4326)::GEOGRAPHY, 'Athwalines, Surat', dept_police),
        (cam_p3, sys_police, 'cam-pol-03', 'Vadodara Junction',   ST_SetSRID(ST_MakePoint(73.2090, 22.3072), 4326)::GEOGRAPHY, 'Sayajiganj, Vadodara', dept_police),
        (cam_p4, sys_police, 'cam-pol-04', 'Rajkot Ring Road',    ST_SetSRID(ST_MakePoint(70.8022, 22.3039), 4326)::GEOGRAPHY, 'Race Course Road, Rajkot', dept_police),
        (cam_p5, sys_police, 'cam-pol-05', 'Gandhinagar Secretariat', ST_SetSRID(ST_MakePoint(72.6849, 23.2156), 4326)::GEOGRAPHY, 'Sector 10, Gandhinagar', dept_police)
    ON CONFLICT (system_id, external_id) DO NOTHING;

    -- ── 3. Federated Cameras — RTO (4 cameras) ───────────────

    INSERT INTO federated_cameras (id, system_id, external_id, name, location, location_label, department_id)
    VALUES
        (cam_r1, sys_rto, 'rto-nh48-01',  'NH-48 Toll Plaza',          ST_SetSRID(ST_MakePoint(72.4426, 22.9930), 4326)::GEOGRAPHY, 'NH-48 Ahmedabad-Mumbai', dept_rto),
        (cam_r2, sys_rto, 'rto-nh8-01',   'NH-8 Checkpoint',           ST_SetSRID(ST_MakePoint(72.5494, 23.0721), 4326)::GEOGRAPHY, 'NH-8 Gandhinagar Highway', dept_rto),
        (cam_r3, sys_rto, 'rto-sh17-01',  'SH-17 Himatnagar Toll',     ST_SetSRID(ST_MakePoint(72.9638, 23.5995), 4326)::GEOGRAPHY, 'SH-17 Himatnagar', dept_rto),
        (cam_r4, sys_rto, 'rto-exp-01',   'Expressway Navsari Entry',  ST_SetSRID(ST_MakePoint(72.9520, 20.9467), 4326)::GEOGRAPHY, 'Navsari Expressway Entry', dept_rto)
    ON CONFLICT (system_id, external_id) DO NOTHING;

    -- ── 4. Federated Cameras — Municipal (4 cameras) ─────────

    INSERT INTO federated_cameras (id, system_id, external_id, name, location, location_label, department_id)
    VALUES
        (cam_m1, sys_muni, 'amc-lal-01',  'Lal Darwaja Intersection',  ST_SetSRID(ST_MakePoint(72.5868, 23.0227), 4326)::GEOGRAPHY, 'Lal Darwaja, Ahmedabad', dept_muni),
        (cam_m2, sys_muni, 'amc-brts-01', 'BRTS Kalupur',              ST_SetSRID(ST_MakePoint(72.5987, 23.0290), 4326)::GEOGRAPHY, 'Kalupur Railway Station BRTS', dept_muni),
        (cam_m3, sys_muni, 'amc-mani-01', 'Maninagar Market',          ST_SetSRID(ST_MakePoint(72.6059, 22.9972), 4326)::GEOGRAPHY, 'Maninagar, Ahmedabad', dept_muni),
        (cam_m4, sys_muni, 'amc-cgrd-01', 'CG Road Flyover',           ST_SetSRID(ST_MakePoint(72.5566, 23.0389), 4326)::GEOGRAPHY, 'CG Road, Ahmedabad', dept_muni)
    ON CONFLICT (system_id, external_id) DO NOTHING;

    -- ── 5. Seed Events (10 rows, mix of 3 systems) ───────────
    -- ev3 and ev7 contain the watchlisted plate GJ01CD5678 to demo alert

    INSERT INTO federated_events (id, system_id, camera_id, event_type, detected_plate, confidence, vehicle_type, raw_payload, received_at, source_timestamp)
    VALUES
        (ev1,  sys_police, cam_p1, 'vehicle_detection', 'GJ05AB1234', 0.92, 'car',
         '{"alarmType":"LPR","licensePlate":"GJ05AB1234","deviceId":"cam-pol-01","score":0.92}',
         now() - interval '25 min', now() - interval '25 min'),

        (ev2,  sys_police, cam_p2, 'vehicle_detection', 'GJ01XX9999', 0.88, 'truck',
         '{"alarmType":"LPR","licensePlate":"GJ01XX9999","deviceId":"cam-pol-02","score":0.88}',
         now() - interval '22 min', now() - interval '22 min'),

        (ev3,  sys_police, cam_p3, 'vehicle_detection', 'GJ01CD5678', 0.95, 'car',
         '{"alarmType":"LPR","licensePlate":"GJ01CD5678","deviceId":"cam-pol-03","score":0.95}',
         now() - interval '20 min', now() - interval '20 min'),

        (ev4,  sys_rto,    cam_r1, 'vehicle_detection', 'GJ05AB1234', 0.87, 'car',
         '{"eventType":"vehicleDetection","plateText":"GJ05AB1234","cameraIndex":"rto-nh48-01","detectionConfidence":0.87}',
         now() - interval '18 min', now() - interval '18 min'),

        (ev5,  sys_rto,    cam_r2, 'vehicle_detection', 'GJ01KK7777', 0.81, 'motorcycle',
         '{"eventType":"vehicleDetection","plateText":"GJ01KK7777","cameraIndex":"rto-nh8-01","detectionConfidence":0.81}',
         now() - interval '15 min', now() - interval '15 min'),

        (ev6,  sys_muni,   cam_m1, 'vehicle_detection', 'GJ01AB0001', 0.79, 'car',
         '{"type":"ANPR","plate":"GJ01AB0001","channel":"amc-lal-01","conf":0.79}',
         now() - interval '12 min', now() - interval '12 min'),

        (ev7,  sys_muni,   cam_m2, 'vehicle_detection', 'GJ01CD5678', 0.84, 'car',
         '{"type":"ANPR","plate":"GJ01CD5678","channel":"amc-brts-01","conf":0.84}',
         now() - interval '10 min', now() - interval '10 min'),

        (ev8,  sys_police, cam_p4, 'vehicle_detection', 'GJ04ZZ3210', 0.76, 'car',
         '{"alarmType":"LPR","licensePlate":"GJ04ZZ3210","deviceId":"cam-pol-04","score":0.76}',
         now() - interval '8 min', now() - interval '8 min'),

        (ev9,  sys_rto,    cam_r3, 'vehicle_detection', 'GJ05AB1234', 0.91, 'car',
         '{"eventType":"vehicleDetection","plateText":"GJ05AB1234","cameraIndex":"rto-sh17-01","detectionConfidence":0.91}',
         now() - interval '5 min', now() - interval '5 min'),

        (ev10, sys_muni,   cam_m3, 'vehicle_detection', 'GJ07PQ4444', 0.83, 'truck',
         '{"type":"ANPR","plate":"GJ07PQ4444","channel":"amc-mani-01","conf":0.83}',
         now() - interval '2 min', now() - interval '2 min')
    ON CONFLICT (id) DO NOTHING;

    -- ── 6. Seed Correlation Results ──────────────────────────
    -- GJ05AB1234 seen by Police (ev1) → RTO NH-48 (ev4) → RTO SH-17 (ev9)

    INSERT INTO correlation_results (id, plate_number, event_ids, system_ids, first_seen, last_seen, travel_time_secs, camera_sequence, is_watchlisted)
    VALUES (
        'e0000001-0000-0000-0000-000000000001',
        'GJ05AB1234',
        ARRAY[ev1, ev4, ev9],
        ARRAY[sys_police, sys_rto],
        now() - interval '25 min',
        now() - interval '5 min',
        1200,
        '[
          {"camera_name":"Ahmedabad Police HQ","system_name":"Gujarat Police VMS (Milestone)","timestamp_offset":"-25 min","lat":23.0225,"lng":72.5714},
          {"camera_name":"NH-48 Toll Plaza","system_name":"Gujarat RTO Checkpoint System (HikCentral)","timestamp_offset":"-18 min","lat":22.9930,"lng":72.4426},
          {"camera_name":"SH-17 Himatnagar Toll","system_name":"Gujarat RTO Checkpoint System (HikCentral)","timestamp_offset":"-5 min","lat":23.5995,"lng":72.9638}
        ]'::JSONB,
        false
    )
    ON CONFLICT (id) DO NOTHING;

    -- GJ01CD5678 seen by Police (ev3) then Municipal (ev7) — this IS watchlisted
    INSERT INTO correlation_results (id, plate_number, event_ids, system_ids, first_seen, last_seen, travel_time_secs, camera_sequence, is_watchlisted)
    VALUES (
        'e0000002-0000-0000-0000-000000000002',
        'GJ01CD5678',
        ARRAY[ev3, ev7],
        ARRAY[sys_police, sys_muni],
        now() - interval '20 min',
        now() - interval '10 min',
        600,
        '[
          {"camera_name":"Vadodara Junction","system_name":"Gujarat Police VMS (Milestone)","timestamp_offset":"-20 min","lat":22.3072,"lng":73.2090},
          {"camera_name":"BRTS Kalupur","system_name":"AMC City Surveillance (Dahua)","timestamp_offset":"-10 min","lat":23.0290,"lng":72.5987}
        ]'::JSONB,
        true
    )
    ON CONFLICT (id) DO NOTHING;

END $$;
