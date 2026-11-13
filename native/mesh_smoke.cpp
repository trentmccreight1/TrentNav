#include <Recast.h>
#include <DetourAlloc.h>
#include <DetourNavMesh.h>
#include <DetourNavMeshBuilder.h>
#include <DetourNavMeshQuery.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    void require(bool success, char const* stage)
    {
        if (!success)
            throw std::runtime_error(stage);
    }

    std::array<float, 3> center(dtMeshTile const& tile, int polygon)
    {
        auto const& selected = tile.polys[polygon];
        std::array<float, 3> position = {};
        for (unsigned char vertex = 0; vertex < selected.vertCount; ++vertex)
            for (int axis = 0; axis < 3; ++axis)
                position[axis] += tile.verts[selected.verts[vertex] * 3 + axis];
        for (auto& coordinate : position)
            coordinate /= selected.vertCount;
        return position;
    }
}

int main(int argc, char** arguments)
{
    try
    {
        require(argc == 3, "Usage: trentnav_mesh_smoke <diagnostic-geometry.obj> <new-output.tnavdiag>");
        std::filesystem::path inputPath(arguments[1]);
        std::filesystem::path outputPath(arguments[2]);
        require(std::filesystem::file_size(inputPath) <= 128 * 1024 * 1024, "Input exceeds diagnostic size limit");
        require(outputPath.extension() == ".tnavdiag" && !std::filesystem::exists(outputPath), "Refusing non-diagnostic output or overwrite");
        std::ifstream input(inputPath);
        require(static_cast<bool>(input), "Cannot open diagnostic geometry");
        std::vector<float> vertices;
        std::vector<int> triangles;
        std::vector<std::array<float, 4>> blockedColumns;
        std::string line;
        bool diagnosticObject = false;
        while (std::getline(input, line))
        {
            require(line.size() < 1024, "Geometry line is too long");
            std::istringstream fields(line);
            std::string kind;
            if (!(fields >> kind))
                continue;
            if (kind == "o")
            {
                std::string name;
                require(!diagnosticObject && static_cast<bool>(fields >> name)
                    && (name == "diagnostic_terrain_only" || name == "diagnostic_combined_collision"), "Expected our diagnostic geometry object");
                diagnosticObject = true;
            }
            else if (kind == "v")
            {
                float worldX, worldY, height;
                require(static_cast<bool>(fields >> worldX >> worldY >> height), "Invalid geometry vertex");
                require(std::isfinite(worldX) && std::isfinite(worldY) && std::isfinite(height) && std::abs(worldX) < 100000 && std::abs(worldY) < 100000 && std::abs(height) < 100000, "Invalid geometry coordinate");
                vertices.insert(vertices.end(), {worldX, height, -worldY});
                require(vertices.size() <= 1000000 * 3, "Too many diagnostic vertices");
            }
            else if (kind == "b")
            {
                std::array<float, 4> bounds{};
                require(static_cast<bool>(fields >> bounds[0] >> bounds[1] >> bounds[2] >> bounds[3]), "Invalid blocked column");
                for (float coordinate : bounds)
                    require(std::isfinite(coordinate) && std::abs(coordinate) < 100000, "Invalid blocked column coordinate");
                require(bounds[0] < bounds[2] && bounds[1] < bounds[3] && blockedColumns.size() < 4096, "Invalid or excessive blocked columns");
                blockedColumns.push_back(bounds);
            }
            else if (kind == "f")
            {
                int first, second, third;
                require(static_cast<bool>(fields >> first >> second >> third), "Invalid triangle indices");
                require(first > 0 && second > 0 && third > 0 && first != second && second != third && first != third, "Invalid triangle identity");
                triangles.insert(triangles.end(), {first - 1, second - 1, third - 1});
                require(triangles.size() <= 2000000 * 3, "Too many diagnostic triangles");
            }
            else
                throw std::runtime_error("Unsupported geometry record");
            std::string extra;
            require(!(fields >> extra), "Unexpected geometry fields");
        }
        require(input.eof() && diagnosticObject && !vertices.empty() && !triangles.empty(), "Incomplete diagnostic geometry");
        int vertexCount = static_cast<int>(vertices.size() / 3);
        int triangleCount = static_cast<int>(triangles.size() / 3);
        for (int index : triangles)
            require(index < vertexCount, "Triangle references a missing vertex");

        rcContext context;
        rcConfig config = {};
        config.cs = 0.5f;
        config.ch = 0.2f;
        config.walkableSlopeAngle = 45;
        config.walkableHeight = 10;
        config.walkableClimb = 3;
        config.walkableRadius = 1;
        config.maxEdgeLen = 24;
        config.maxSimplificationError = 1.3f;
        config.minRegionArea = 64;
        config.mergeRegionArea = 400;
        config.maxVertsPerPoly = 6;
        config.detailSampleDist = 3;
        config.detailSampleMaxError = 0.2f;
        rcCalcBounds(vertices.data(), vertexCount, config.bmin, config.bmax);
        config.bmin[1] -= 2;
        config.bmax[1] += 4;
        require(config.bmax[1] - config.bmin[1] <= 1024, "Diagnostic vertical range exceeds rasterization limit");
        rcCalcGridSize(config.bmin, config.bmax, config.cs, &config.width, &config.height);
        require(config.width > 0 && config.height > 0 && config.width <= 1200 && config.height <= 1200, "Diagnostic geometry exceeds one bounded tile");
        std::unique_ptr<rcHeightfield, decltype(&rcFreeHeightField)> field(rcAllocHeightfield(), rcFreeHeightField);
        require(field && rcCreateHeightfield(&context, *field, config.width, config.height, config.bmin, config.bmax, config.cs, config.ch), "Heightfield allocation failed");
        std::vector<unsigned char> areas(triangleCount, RC_NULL_AREA);
        rcMarkWalkableTriangles(&context, config.walkableSlopeAngle, vertices.data(), vertexCount, triangles.data(), triangleCount, areas.data());
        require(rcRasterizeTriangles(&context, vertices.data(), vertexCount, triangles.data(), areas.data(), triangleCount, *field, config.walkableClimb), "Terrain rasterization failed");
        rcFilterLowHangingWalkableObstacles(&context, config.walkableClimb, *field);
        rcFilterLedgeSpans(&context, config.walkableHeight, config.walkableClimb, *field);
        rcFilterWalkableLowHeightSpans(&context, config.walkableHeight, *field);
        std::unique_ptr<rcCompactHeightfield, decltype(&rcFreeCompactHeightfield)> compact(rcAllocCompactHeightfield(), rcFreeCompactHeightfield);
        require(compact && rcBuildCompactHeightfield(&context, config.walkableHeight, config.walkableClimb, *field, *compact), "Compact heightfield failed");
        field.reset();
        for (auto const& bounds : blockedColumns)
        {
            std::array<float, 3> minimum{bounds[0] - config.cs, config.bmin[1], -bounds[3] - config.cs};
            std::array<float, 3> maximum{bounds[2] + config.cs, config.bmax[1], -bounds[1] + config.cs};
            rcMarkBoxArea(&context, minimum.data(), maximum.data(), RC_NULL_AREA, *compact);
        }
        require(rcErodeWalkableArea(&context, config.walkableRadius, *compact), "Agent erosion failed");
        require(rcBuildDistanceField(&context, *compact) && rcBuildRegions(&context, *compact, 0, config.minRegionArea, config.mergeRegionArea), "Region generation failed");
        std::unique_ptr<rcContourSet, decltype(&rcFreeContourSet)> contours(rcAllocContourSet(), rcFreeContourSet);
        require(contours && rcBuildContours(&context, *compact, config.maxSimplificationError, config.maxEdgeLen, *contours), "Contour generation failed");
        std::unique_ptr<rcPolyMesh, decltype(&rcFreePolyMesh)> polygons(rcAllocPolyMesh(), rcFreePolyMesh);
        require(polygons && rcBuildPolyMesh(&context, *contours, config.maxVertsPerPoly, *polygons) && polygons->npolys > 0, "Polygon mesh failed");
        std::unique_ptr<rcPolyMeshDetail, decltype(&rcFreePolyMeshDetail)> detail(rcAllocPolyMeshDetail(), rcFreePolyMeshDetail);
        require(detail && rcBuildPolyMeshDetail(&context, *polygons, *compact, config.detailSampleDist, config.detailSampleMaxError, *detail), "Detail mesh failed");
        for (int polygon = 0; polygon < polygons->npolys; ++polygon)
        {
            polygons->areas[polygon] = 0;
            polygons->flags[polygon] = 1;
        }
        dtNavMeshCreateParams parameters = {};
        parameters.verts = polygons->verts;
        parameters.vertCount = polygons->nverts;
        parameters.polys = polygons->polys;
        parameters.polyAreas = polygons->areas;
        parameters.polyFlags = polygons->flags;
        parameters.polyCount = polygons->npolys;
        parameters.nvp = polygons->nvp;
        parameters.detailMeshes = detail->meshes;
        parameters.detailVerts = detail->verts;
        parameters.detailVertsCount = detail->nverts;
        parameters.detailTris = detail->tris;
        parameters.detailTriCount = detail->ntris;
        parameters.walkableHeight = 2;
        parameters.walkableRadius = 0.5f;
        parameters.walkableClimb = 0.6f;
        std::copy_n(polygons->bmin, 3, parameters.bmin);
        std::copy_n(polygons->bmax, 3, parameters.bmax);
        parameters.cs = config.cs;
        parameters.ch = config.ch;
        parameters.buildBvTree = true;
        unsigned char* rawData = nullptr;
        int dataSize = 0;
        require(dtCreateNavMeshData(&parameters, &rawData, &dataSize), "Detour serialization failed");
        std::unique_ptr<unsigned char, decltype(&dtFree)> serialized(rawData, dtFree);
        dtNavMesh mesh;
        require(dtStatusSucceed(mesh.init(serialized.get(), dataSize, 0)), "Detour mesh initialization failed");
        auto const* tile = static_cast<dtNavMesh const&>(mesh).getTile(0);
        require(tile && tile->header, "Missing diagnostic mesh tile");
        int startPolygon = -1;
        for (int index = 0; index < tile->header->polyCount && startPolygon < 0; ++index)
            for (unsigned char edge = 0; edge < tile->polys[index].vertCount; ++edge)
                if (tile->polys[index].neis[edge] && !(tile->polys[index].neis[edge] & DT_EXT_LINK))
                {
                    startPolygon = index;
                    break;
                }
        require(startPolygon >= 0, "No connected diagnostic polygon pair");
        std::vector<int> visited{startPolygon};
        std::vector<bool> seen(tile->header->polyCount, false);
        seen[startPolygon] = true;
        for (std::size_t next = 0; next < visited.size() && visited.size() < 128; ++next)
        {
            auto const& polygon = tile->polys[visited[next]];
            for (unsigned char edge = 0; edge < polygon.vertCount; ++edge)
            {
                unsigned short neighbor = polygon.neis[edge];
                if (neighbor && !(neighbor & DT_EXT_LINK) && !seen[neighbor - 1])
                {
                    seen[neighbor - 1] = true;
                    visited.push_back(neighbor - 1);
                }
            }
        }
        require(visited.size() > 1, "No connected diagnostic polygon pair");
        dtPolyRef base = mesh.getPolyRefBase(tile);
        dtPolyRef startRef = base | static_cast<dtPolyRef>(startPolygon);
        dtPolyRef endRef = base | static_cast<dtPolyRef>(visited.back());
        auto start = center(*tile, startPolygon);
        auto end = center(*tile, visited.back());
        dtNavMeshQuery query;
        require(dtStatusSucceed(query.init(&mesh, 4096)), "Query allocation failed");
        dtQueryFilter filter;
        std::array<dtPolyRef, 256> path;
        int pathCount = 0;
        dtStatus status = query.findPath(startRef, endRef, start.data(), end.data(), &filter, path.data(), &pathCount, static_cast<int>(path.size()));
        require(dtStatusSucceed(status) && !dtStatusDetail(status, DT_PARTIAL_RESULT | DT_BUFFER_TOO_SMALL | DT_OUT_OF_NODES) && pathCount > 1 && path[pathCount - 1] == endRef, "Diagnostic path was incomplete");
        if (!outputPath.parent_path().empty())
            std::filesystem::create_directories(outputPath.parent_path());
        std::ofstream output(outputPath, std::ios::binary);
        std::array<std::uint32_t, 4> header{1, 70888, 0, static_cast<std::uint32_t>(dataSize)};
        output.write("TNVDIAG1", 8);
        output.write(reinterpret_cast<char const*>(header.data()), sizeof(header));
        output.write(reinterpret_cast<char const*>(serialized.get()), dataSize);
        output.close();
        require(static_cast<bool>(output), "Diagnostic output write failed");
        std::cout << "status=diagnostic_mesh_complete\npolygons=" << polygons->npolys << "\nmesh_bytes=" << dataSize << "\nquery_path_polygons=" << pathCount << "\nblocked_columns=" << blockedColumns.size() << "\nnavigation_ready=false\n";
        return 0;
    }
    catch (std::exception const& error)
    {
        std::cerr << "Mesh smoke test failed: " << error.what() << "\n";
        return 1;
    }
}
