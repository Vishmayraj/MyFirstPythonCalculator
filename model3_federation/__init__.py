"""
Model 3 — VMS Federation & Middleware Integration
--------------------------------------------------
Provides the adapter framework, event bus, correlation engine,
and REST/WebSocket API for federating multiple departmental VMS
platforms (Police/Milestone, RTO/HikCentral, Municipal/Dahua) into
a single unified metadata layer.

Raw video stays at the departmental edge; only lightweight JSON
detection events travel to the central bus — this is the core
architectural argument for handling ~80,000 cameras statewide without
saturating the state network backbone.
"""
