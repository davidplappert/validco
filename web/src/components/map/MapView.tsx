"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import type { Route } from "@/lib/types";
import { surfaceColour } from "./surfaceColours";

/**
 * The map.
 *
 * MapLibre GL with raster OpenStreetMap tiles — no API key, no token, nothing
 * billable. The interesting choice is that a route is drawn as one line *per
 * surface class* rather than a single polyline, because "how much of this walk
 * is in the roadway" is a question the product answers, and a colour-coded line
 * answers it at a glance.
 */

const ROUTE_SOURCE = "route-surfaces";
const ORIGIN_SOURCE = "origin-point";
const DESTINATION_SOURCE = "destination-point";

interface Props {
  route: Route | null;
  origin: { lat: number; lon: number } | null;
  center: [number, number];
}

/** The MapLibre style: a dimmed OSM raster basemap, no vector tiles. */
function buildStyle(): maplibregl.StyleSpecification {
  return {
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
          // Desaturate and dim the basemap so the coloured route reads as the
          // foreground instead of competing with the tiles.
          "raster-saturation": -0.55,
          "raster-brightness-max": 0.82,
          "raster-contrast": -0.08,
        },
      },
    ],
  };
}

/** An empty GeoJSON collection, used to initialise and to clear sources. */
function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

export default function MapView({ route, origin, center }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);

  // Create the map exactly once. Re-creating it when props change is the usual
  // way a MapLibre component leaks WebGL contexts until the tab dies.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: [center[1], center[0]],
      zoom: 13,
      attributionControl: { compact: true },
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "top-left");

    map.on("load", () => {
      for (const id of [ROUTE_SOURCE, ORIGIN_SOURCE, DESTINATION_SOURCE]) {
        map.addSource(id, { type: "geojson", data: emptyCollection() });
      }

      // A dark casing under the coloured line keeps it legible where the route
      // crosses pale features such as parks or water.
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
            "path",
            surfaceColour("path"),
            "sidewalk",
            surfaceColour("sidewalk"),
            "crossing",
            surfaceColour("crossing"),
            "road",
            surfaceColour("road"),
            surfaceColour("unknown"),
          ],
          "line-width": 4.5,
        },
      });
      map.addLayer({
        id: "destination-dot",
        type: "circle",
        source: DESTINATION_SOURCE,
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

  // Push route and marker changes into the existing sources.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const apply = () => {
      const routeSource = map.getSource(ROUTE_SOURCE) as maplibregl.GeoJSONSource | undefined;
      const originSource = map.getSource(ORIGIN_SOURCE) as maplibregl.GeoJSONSource | undefined;
      const destSource = map.getSource(DESTINATION_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (!routeSource || !originSource || !destSource) return;

      routeSource.setData({
        type: "FeatureCollection",
        features: (route?.geometry.segments ?? []).map((segment) => ({
          type: "Feature" as const,
          properties: { surface: segment.surface },
          geometry: { type: "LineString" as const, coordinates: segment.coordinates },
        })),
      });

      originSource.setData(
        origin
          ? {
              type: "FeatureCollection",
              features: [
                {
                  type: "Feature" as const,
                  properties: {},
                  geometry: { type: "Point" as const, coordinates: [origin.lon, origin.lat] },
                },
              ],
            }
          : emptyCollection(),
      );

      destSource.setData(
        route?.destination
          ? {
              type: "FeatureCollection",
              features: [
                {
                  type: "Feature" as const,
                  properties: { name: route.destination.name },
                  geometry: {
                    type: "Point" as const,
                    coordinates: [route.destination.lon, route.destination.lat],
                  },
                },
              ],
            }
          : emptyCollection(),
      );

      const coordinates = route?.geometry.coordinates ?? [];
      if (coordinates.length > 1) {
        const first = coordinates[0] as [number, number];
        const bounds = coordinates.reduce(
          (accumulated, position) => accumulated.extend(position as [number, number]),
          new maplibregl.LngLatBounds(first, first),
        );
        map.fitBounds(bounds, { padding: 70, duration: 700, maxZoom: 16 });
      } else if (origin) {
        map.easeTo({ center: [origin.lon, origin.lat], zoom: 15, duration: 700 });
      }
    };

    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [route, origin]);

  return (
    <div
      ref={containerRef}
      className="absolute inset-0"
      aria-label="Route map"
      role="application"
    />
  );
}
