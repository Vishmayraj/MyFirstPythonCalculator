/**
 * Sentinel — Map Dashboard (static/js/map.js)
 *
 * Leaflet map centered on Gujarat, markers from /api/v1/cameras,
 * clustered via Leaflet.markercluster.
 *
 * Department layer-toggle + district dropdown → re-fetch + redraw.
 * Uses plain fetch(), NOT HTMX (see implementation plan Phase 4 note).
 */

function mapDashboard() {
    return {
        map: null,
        clusterGroup: null,
        allCameras: [],
        departments: [],
        districts: [],
        selectedDistrict: '',
        activeDepartments: new Set(),
        gapOverlayLayer: null,
        showGapOverlay: false,
        districtBoundaryLayer: null,
        showDistrictBoundaries: true,

        async init() {
            // Check if map container is already initialized (fixes hot-reloading / double-init Alpine errors)
            const mapContainer = document.getElementById('map');
            if (mapContainer && mapContainer._leaflet_id) {
                mapContainer._leaflet_id = null;
            }

            // Initialise Leaflet map centered on Gujarat
            this.map = L.map('map', {
                zoomControl: true,
                attributionControl: true,
            }).setView([22.3, 72.0], 7);

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                maxZoom: 19,
            }).addTo(this.map);

            // Create cluster group
            this.clusterGroup = L.markerClusterGroup({
                showCoverageOnHover: false,
                maxClusterRadius: 50,
                spiderfyOnMaxZoom: true,
                disableClusteringAtZoom: 16,
            });
            this.map.addLayer(this.clusterGroup);

            // Load departments and districts for controls
            await Promise.all([
                this.loadDepartments(),
                this.loadDistricts(),
            ]);

            // Draw real district boundary polygons (on by default)
            this.renderDistrictBoundaries();

            // Load cameras and render
            await this.loadAndRender();
        },

        async loadDepartments() {
            try {
                const res = await fetch('/api/v1/departments');
                this.departments = await res.json();

                // Build department checkboxes
                const container = document.getElementById('department-checkboxes');
                container.innerHTML = '';

                this.departments.forEach(dept => {
                    this.activeDepartments.add(dept.id);

                    const label = document.createElement('label');
                    label.className = 'checkbox-item';

                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.checked = true;
                    cb.value = dept.id;
                    cb.addEventListener('change', () => {
                        if (cb.checked) {
                            this.activeDepartments.add(dept.id);
                        } else {
                            this.activeDepartments.delete(dept.id);
                        }
                        this.renderMarkers();
                    });

                    const span = document.createElement('span');
                    span.textContent = dept.name;

                    label.appendChild(cb);
                    label.appendChild(span);
                    container.appendChild(label);
                });

                // Add "No Department" toggle for cameras without department_id
                const noDeptLabel = document.createElement('label');
                noDeptLabel.className = 'checkbox-item';
                const noDeptCb = document.createElement('input');
                noDeptCb.type = 'checkbox';
                noDeptCb.checked = true;
                noDeptCb.value = '__none__';
                this.activeDepartments.add('__none__');
                noDeptCb.addEventListener('change', () => {
                    if (noDeptCb.checked) {
                        this.activeDepartments.add('__none__');
                    } else {
                        this.activeDepartments.delete('__none__');
                    }
                    this.renderMarkers();
                });
                const noDeptSpan = document.createElement('span');
                noDeptSpan.textContent = 'Unassigned';
                noDeptLabel.appendChild(noDeptCb);
                noDeptLabel.appendChild(noDeptSpan);
                container.appendChild(noDeptLabel);

            } catch (e) {
                console.error('Failed to load departments:', e);
            }
        },

        async loadDistricts() {
            try {
                const res = await fetch('/api/v1/districts');
                this.districts = await res.json();

                const select = document.getElementById('district-filter');
                // Keep the "All Districts" option
                this.districts.forEach(d => {
                    const opt = document.createElement('option');
                    opt.value = d.id;
                    opt.textContent = d.name;
                    select.appendChild(opt);
                });
            } catch (e) {
                console.error('Failed to load districts:', e);
            }
        },

        async loadAndRender() {
            try {
                let url = '/api/v1/cameras';
                const params = new URLSearchParams();
                if (this.selectedDistrict) {
                    params.set('district_id', this.selectedDistrict);
                }
                if (params.toString()) {
                    url += '?' + params.toString();
                }
                const res = await fetch(url);
                this.allCameras = await res.json();
                this.renderMarkers();
            } catch (e) {
                console.error('Failed to load cameras:', e);
            }
        },

        filterByDistrict(districtId) {
            this.selectedDistrict = districtId;
            this.loadAndRender();
            document.getElementById('district-filter').value = districtId;
        },

        // Draws each district's real PostGIS polygon (shared/db/seed.sql —
        // actual Gujarat district shapes, not bounding boxes) as a Leaflet
        // GeoJSON layer, click-to-filter + hover highlight.
        renderDistrictBoundaries() {
            if (this.districtBoundaryLayer) {
                this.map.removeLayer(this.districtBoundaryLayer);
                this.districtBoundaryLayer = null;
            }

            const featureCollection = {
                type: 'FeatureCollection',
                features: this.districts
                    .filter(d => d.boundary)
                    .map(d => ({
                        type: 'Feature',
                        properties: { id: d.id, name: d.name, camera_count: d.camera_count },
                        geometry: d.boundary,
                    })),
            };

            if (featureCollection.features.length === 0) return;

            const baseStyle = { color: '#3b82f6', weight: 1.25, opacity: 0.55, fillOpacity: 0.03, fillColor: '#3b82f6' };
            const hoverStyle = { weight: 2.5, opacity: 0.9, fillOpacity: 0.12 };

            this.districtBoundaryLayer = L.geoJSON(featureCollection, {
                style: () => ({ ...baseStyle }),
                onEachFeature: (feature, layer) => {
                    layer.bindTooltip(feature.properties.name, { sticky: true, className: 'district-tooltip' });
                    layer.on('mouseover', () => layer.setStyle(hoverStyle));
                    layer.on('mouseout', () => layer.setStyle(baseStyle));
                    layer.on('click', () => this.filterByDistrict(feature.properties.id));
                },
            });

            if (this.showDistrictBoundaries) {
                this.districtBoundaryLayer.addTo(this.map);
                // Keep boundaries under markers/gap overlay so popups/clusters stay clickable on top.
                this.districtBoundaryLayer.bringToBack();
            }
        },

        toggleDistrictBoundaries(enable) {
            this.showDistrictBoundaries = enable;
            if (!this.districtBoundaryLayer) return;
            if (enable) {
                this.districtBoundaryLayer.addTo(this.map);
                this.districtBoundaryLayer.bringToBack();
            } else {
                this.map.removeLayer(this.districtBoundaryLayer);
            }
        },

        renderMarkers() {
            this.clusterGroup.clearLayers();

            let online = 0, offline = 0, maintenance = 0, total = 0;

            const statusEmoji = {
                'online': '🟢',
                'offline': '🔴',
                'maintenance': '🟡',
            };

            this.allCameras.forEach(cam => {
                // Skip cameras without location
                if (!cam.location) return;

                // Department filter
                const deptId = cam.department_id || '__none__';
                if (!this.activeDepartments.has(deptId)) return;

                total++;
                if (cam.connectivity_status === 'online') online++;
                else if (cam.connectivity_status === 'offline') offline++;
                else maintenance++;

                const [lon, lat] = cam.location.coordinates;
                const emoji = statusEmoji[cam.connectivity_status] || '⚪';

                const icon = L.divIcon({
                    html: `<span style="font-size: 1.4rem; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">${emoji}</span>`,
                    className: 'sentinel-marker',
                    iconSize: [28, 28],
                    iconAnchor: [14, 14],
                    popupAnchor: [0, -14],
                });

                const marker = L.marker([lat, lon], { icon });

                const escapeHtml = (unsafe) => {
                    return (unsafe || "").toString()
                         .replace(/&/g, "&amp;")
                         .replace(/</g, "&lt;")
                         .replace(/>/g, "&gt;")
                         .replace(/"/g, "&quot;")
                         .replace(/'/g, "&#039;");
                };

                // Build popup
                let popupHtml = `
                    <div class="popup-content">
                        <div class="popup-title">${emoji} ${escapeHtml(cam.name)}</div>
                        <div class="popup-row">
                            <span class="popup-label">Department</span>
                            <span class="popup-value">${cam.department_name || '—'}</span>
                        </div>
                        <div class="popup-row">
                            <span class="popup-label">District</span>
                            <span class="popup-value">${cam.district_name || '—'}</span>
                        </div>
                        <div class="popup-row">
                            <span class="popup-label">Type</span>
                            <span class="popup-value">${cam.camera_type || '—'}</span>
                        </div>
                        <div class="popup-row">
                            <span class="popup-label">Ownership</span>
                            <span class="popup-value">${cam.ownership || '—'}</span>
                        </div>
                        <div class="popup-row">
                            <span class="popup-label">Status</span>
                            <span class="popup-value">
                                <span class="badge badge--${cam.connectivity_status}">
                                    <span class="badge-dot badge-dot--${cam.connectivity_status}"></span>
                                    ${cam.connectivity_status}
                                </span>
                            </span>
                        </div>
                        <div class="popup-row">
                            <span class="popup-label">Storage</span>
                            <span class="popup-value">${cam.storage_type || '—'}${cam.retention_days ? ' · ' + cam.retention_days + 'd' : ''}</span>
                        </div>`;

                if (cam.vms_url) {
                    popupHtml += `
                        <a href="${cam.vms_url}" target="_blank" rel="noopener" class="popup-link">
                            🖥️ Open VMS Viewer
                        </a>`;
                }

                popupHtml += `</div>`;

                marker.bindPopup(popupHtml, { maxWidth: 300, minWidth: 240 });
                this.clusterGroup.addLayer(marker);
            });

            // Update stats
            document.getElementById('stat-online').textContent = online;
            document.getElementById('stat-offline').textContent = offline;
            document.getElementById('stat-maintenance').textContent = maintenance;
            document.getElementById('stat-total').textContent = total;
        },

        async toggleGapOverlay(enable) {
            this.showGapOverlay = enable;
            if (!enable) {
                if (this.gapOverlayLayer) {
                    this.map.removeLayer(this.gapOverlayLayer);
                    this.gapOverlayLayer = null;
                }
                return;
            }

            try {
                const res = await fetch('/api/v1/gap-analysis');
                const gapData = await res.json();

                if (this.gapOverlayLayer) {
                    this.map.removeLayer(this.gapOverlayLayer);
                }

                const featureCollection = {
                    type: 'FeatureCollection',
                    features: []
                };

                gapData.forEach(item => {
                    if (item.uncovered_geojson) {
                        featureCollection.features.push({
                            type: 'Feature',
                            properties: {
                                district_name: item.district_name,
                                camera_count: item.camera_count,
                                coverage_pct: item.coverage_pct,
                                uncovered_area_sq_km: item.uncovered_area_sq_km
                            },
                            geometry: item.uncovered_geojson
                        });
                    }
                });

                this.gapOverlayLayer = L.geoJSON(featureCollection, {
                    style: function(feature) {
                        return {
                            color: '#ef4444',
                            weight: 2,
                            opacity: 0.85,
                            fillColor: '#ef4444',
                            fillOpacity: 0.25
                        };
                    },
                    onEachFeature: function(feature, layer) {
                        const p = feature.properties;
                        layer.bindPopup(`
                            <div class="popup-content">
                                <div class="popup-title">🚨 Uncovered Region</div>
                                <div class="popup-row">
                                    <span class="popup-label">District</span>
                                    <span class="popup-value">${p.district_name}</span>
                                </div>
                                <div class="popup-row">
                                    <span class="popup-label">Cameras</span>
                                    <span class="popup-value">${p.camera_count} active</span>
                                </div>
                                <div class="popup-row">
                                    <span class="popup-label">Coverage</span>
                                    <span class="popup-value">${p.coverage_pct}%</span>
                                </div>
                                <div class="popup-row">
                                    <span class="popup-label">Uncovered Area</span>
                                    <span class="popup-value">${p.uncovered_area_sq_km} sq km</span>
                                </div>
                            </div>
                        `);
                    }
                });

                this.map.addLayer(this.gapOverlayLayer);
            } catch (e) {
                console.error('Failed to load gap-analysis overlay:', e);
            }
        },
    };
}
