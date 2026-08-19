"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import type { Route } from "@/lib/api";

/**
 * The map.
 *
 * MapLibre GL with raster OpenStreetMap tiles — no API key, no token, nothing
 * to bill. The interesting part is that a route is drawn as one line *per
 * surface class* rather than a single polyline, because "how much of this walk
 * is on the road rather than a footpath" is a core question the product answers
 * and a colour-coded line answers it at a glance.
 */

const SURFACE_COLOURS: Record<string, string> = {
  path: "#4ade80",
  sidewalk: "#60a5fa",
  crossing: "#fbbf24",
  road: "#f87171",
};

const ROUTE_SOURCE = "route-surfaces";
const ORIGIN_SOURCE = "origin-point";
const DEST_SOURCE = "destination-point";

interface Props {
  route: Route | null;
  origin: { lat: number; lon: number } | null;
  center: [number, number];
}

export default function MapView({ route, origin, center }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);

  // Create the map exactly once. Re-creating it on prop changes is the classic
  // way to make a MapLibre component leak WebGL contexts.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 19,
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &middot; ' +
              'Places &amp; roads &copy; <a href="https://overturemaps.org">Overture Maps Foundation</a> &middot; ' +
              "Elevation USGS 3DEP",
          },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#0a1d22" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            paint: {
              // Desaturate and dim the basemap so the coloured route reads as
              // the foreground rather than competing with the tiles.
              "raster-saturation": -0.55,
              "raster-brightness-max": 0.82,
              "raster-contrast": -0.08,
            },
          },
        ],
      },
      center: [center[1], center[0]],
      zoom: 13,
      attributionControl: { compact: true },
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    map.on("load", () => {
      map.addSource(ROUTE_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addSource(ORIGIN_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addSource(DEST_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      // A dark casing under the coloured line keeps it legible where the route
      // crosses pale features like parks or water.
      map.addLayer({
        id: "route-casing",
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#06141a", "line-width": 8, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": [
            "match",
            ["get", "surface"],
            "path", SURFACE_COLOURS.path,
            "sidewalk", SURFACE_COLOURS.sidewalk,
            "crossing", SURFACE_COLOURS.crossing,
            "road", SURFACE_COLOURS.road,
            "#94a3b8",
          ],
          "line-width": 4.5,
        },
      });

      map.addLayer({
        id: "destination-dot",
        type: "circle",
        source: DEST_SOURCE,
        paint: {
          "circle-radius": 7,
          "circle-color": "#a78bfa",
          "circle-stroke-color": "#0a1d22",
          "circle-stroke-width": 2.5,
        },
      });
      map.addLayer({
        id: "origin-dot",
        type: "circle",
        source: ORIGIN_SOURCE,
        paint: {
          "circle-radius": 8,
          "circle-color": "#e8f1f2",
          "circle-stroke-color": "#0a1d22",
          "circle-stroke-width": 3,
        },
      });

      readyRef.current = true;
      map.resize();
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, [center]);

  // Push route changes into the existing sources.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const apply = () => {
      const routeSource = map.getSource(ROUTE_SOURCE) as maplibregl.GeoJSONSource | undefined;
      const originSource = map.getSource(ORIGIN_SOURCE) as maplibregl.GeoJSONSource | undefined;
      const destSource = map.getSource(DEST_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (!routeSource || !originSource || !destSource) return;

      routeSource.setData({
        type: "FeatureCollection",
        features: (route?.geometry.segments ?? []).map((seg) => ({
          type: "Feature" as const,
          properties: { surface: seg.surface },
          geometry: { type: "LineString" as const, coordinates: seg.coordinates },
        })),
      });

      originSource.setData({
        type: "FeatureCollection",
        features: origin
          ? [
              {
                type: "Feature" as const,
                properties: {},
                geometry: { type: "Point" as const, coordinates: [origin.lon, origin.lat] },
              },
            ]
          : [],
      });

      destSource.setData({
        type: "FeatureCollection",
        features: route?.destination
          ? [
              {
                type: "Feature" as const,
                properties: { name: route.destination.name },
                geometry: {
                  type: "Point" as const,
                  coordinates: [route.destination.lon, route.destination.lat],
                },
              },
            ]
          : [],
      });

      const coords = route?.geometry.coordinates ?? [];
      if (coords.length > 1) {
        const bounds = coords.reduce(
          (acc, c) => acc.extend(c as [number, number]),
          new maplibregl.LngLatBounds(coords[0] as [number, number], coords[0] as [number, number]),
        );
        map.fitBounds(bounds, { padding: 70, duration: 700, maxZoom: 16 });
      } else if (origin) {
        map.easeTo({ center: [origin.lon, origin.lat], zoom: 15, duration: 700 });
      }
    };

    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [route, origin]);

  return <div ref={containerRef} className="absolute inset-0" aria-label="Route map" />;
}

export { SURFACE_COLOURS };
