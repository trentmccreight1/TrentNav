#include <DetourNavMesh.h>
#include <DetourNavMeshQuery.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
void require(bool condition, char const* message)
{
    if (!condition) throw std::runtime_error(message);
}

std::size_t aligned(std::size_t size) { return (size + 3) & ~std::size_t(3); }

float number(char const* text)
{
    std::size_t used = 0;
    std::string value(text);
    float result = std::stof(value, &used);
    require(used == value.size() && std::isfinite(result) && std::abs(result) <= 100000, "Invalid coordinate");
    return result;
}

bool complete(dtStatus status)
{
    return dtStatusSucceed(status) && !dtStatusDetail(status, DT_PARTIAL_RESULT | DT_BUFFER_TOO_SMALL | DT_OUT_OF_NODES);
}

std::array<float, 3> center(dtMeshTile const& tile, int index)
{
    std::array<float, 3> point{};
    auto const& polygon = tile.polys[index];
    for (unsigned char vertex = 0; vertex < polygon.vertCount; ++vertex)
        for (int axis = 0; axis < 3; ++axis)
            point[axis] += tile.verts[polygon.verts[vertex] * 3 + axis] / polygon.vertCount;
    return point;
}

void validate(unsigned char* data, std::size_t size)
{
    require(size >= sizeof(dtMeshHeader), "Missing Detour header");
    auto const& header = *reinterpret_cast<dtMeshHeader const*>(data);
    require(header.magic == DT_NAVMESH_MAGIC && header.version == DT_NAVMESH_VERSION, "Wrong Detour version");
    require(header.polyCount > 0 && header.polyCount <= 100000 && header.vertCount > 0 && header.vertCount <= 65535,
        "Invalid polygon or vertex count");
    require(header.detailMeshCount == header.polyCount && header.offMeshConCount == 0 && header.offMeshBase == header.polyCount,
        "Only ground diagnostic tiles are supported");
    require(header.maxLinkCount >= header.polyCount && header.maxLinkCount <= 1000000 && header.detailVertCount >= 0
        && header.detailVertCount <= 1000000 && header.detailTriCount >= 0 && header.detailTriCount <= 1000000
        && header.bvNodeCount >= 0 && header.bvNodeCount <= header.polyCount * 2, "Invalid mesh counts");
    for (int axis = 0; axis < 3; ++axis)
        require(std::isfinite(header.bmin[axis]) && std::isfinite(header.bmax[axis]) && header.bmin[axis] <= header.bmax[axis]
            && std::abs(header.bmin[axis]) <= 100000 && std::abs(header.bmax[axis]) <= 100000, "Invalid tile bounds");
    require(std::isfinite(header.bvQuantFactor) && header.bvQuantFactor > 0 && header.bvQuantFactor < 10000, "Invalid BV scale");
    std::size_t offset = aligned(sizeof(dtMeshHeader));
    auto take = [&](std::size_t bytes) {
        std::size_t start = offset;
        offset += aligned(bytes);
        require(offset <= size, "Truncated Detour data");
        return data + start;
    };
    auto const* vertices = reinterpret_cast<float const*>(take(sizeof(float) * 3 * header.vertCount));
    auto const* polygons = reinterpret_cast<dtPoly const*>(take(sizeof(dtPoly) * header.polyCount));
    take(sizeof(dtLink) * header.maxLinkCount);
    auto const* details = reinterpret_cast<dtPolyDetail const*>(take(sizeof(dtPolyDetail) * header.detailMeshCount));
    auto const* detailVertices = reinterpret_cast<float const*>(take(sizeof(float) * 3 * header.detailVertCount));
    auto const* detailTriangles = take(4 * static_cast<std::size_t>(header.detailTriCount));
    auto const* nodes = reinterpret_cast<dtBVNode const*>(take(sizeof(dtBVNode) * header.bvNodeCount));
    require(offset == size, "Unexpected Detour trailing data");
    for (int index = 0; index < header.vertCount * 3; ++index)
        require(std::isfinite(vertices[index]) && std::abs(vertices[index]) <= 100000, "Invalid vertex");
    for (int index = 0; index < header.detailVertCount * 3; ++index)
        require(std::isfinite(detailVertices[index]) && std::abs(detailVertices[index]) <= 100000, "Invalid detail vertex");
    for (int index = 0; index < header.polyCount; ++index)
    {
        auto const& polygon = polygons[index];
        auto const& detail = details[index];
        require(polygon.getType() == DT_POLYTYPE_GROUND && polygon.vertCount >= 3 && polygon.vertCount <= DT_VERTS_PER_POLYGON,
            "Invalid ground polygon");
        for (unsigned char vertex = 0; vertex < polygon.vertCount; ++vertex)
        {
            require(polygon.verts[vertex] < header.vertCount, "Invalid polygon vertex index");
            require(polygon.neis[vertex] <= header.polyCount, "External or invalid polygon neighbor");
        }
        require(static_cast<std::uint64_t>(detail.vertBase) + detail.vertCount <= static_cast<std::uint64_t>(header.detailVertCount)
            && static_cast<std::uint64_t>(detail.triBase) + detail.triCount <= static_cast<std::uint64_t>(header.detailTriCount),
            "Invalid detail range");
        for (unsigned int triangle = detail.triBase; triangle < detail.triBase + detail.triCount; ++triangle)
            for (unsigned int corner = 0; corner < 3; ++corner)
                require(detailTriangles[triangle * 4 + corner] < polygon.vertCount + detail.vertCount, "Invalid detail index");
    }
    for (int index = 0; index < header.bvNodeCount; ++index)
    {
        auto const& node = nodes[index];
        require(node.i >= 0 ? node.i < header.polyCount : -static_cast<std::int64_t>(node.i) <= header.bvNodeCount - index,
            "Invalid BV node");
        for (int axis = 0; axis < 3; ++axis) require(node.bmin[axis] <= node.bmax[axis], "Invalid BV bounds");
    }
}
}

int main(int argc, char** argv)
{
    try
    {
        require(argc == 8 || (argc == 3 && std::string(argv[2]) == "--sample"),
            "Usage: trentnav_mesh_query diagnostic.tnavdiag x y z x y z | --sample");
        auto fileSize = std::filesystem::file_size(argv[1]);
        require(fileSize >= 24 + sizeof(dtMeshHeader) && fileSize <= 64 * 1024 * 1024, "Invalid mesh file size");
        std::ifstream input(argv[1], std::ios::binary);
        std::array<char, 8> magic{};
        std::array<std::uint32_t, 4> envelope{};
        input.read(magic.data(), magic.size());
        input.read(reinterpret_cast<char*>(envelope.data()), sizeof(envelope));
        require(std::string(magic.data(), magic.size()) == "TNVDIAG1" && envelope[0] == 1 && envelope[1] == 70888
            && envelope[2] == 0 && envelope[3] == fileSize - 24, "Not a supported diagnostic mesh");
        std::vector<unsigned char> data(envelope[3]);
        input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
        require(static_cast<bool>(input), "Mesh read failed");
        validate(data.data(), data.size());
        dtNavMesh mesh;
        require(dtStatusSucceed(mesh.init(data.data(), static_cast<int>(data.size()), 0)), "Mesh init failed");
        dtNavMeshQuery query;
        require(dtStatusSucceed(query.init(&mesh, 16384)), "Query init failed");
        dtQueryFilter filter;
        filter.setIncludeFlags(1);
        std::array<float, 3> start{}, finish{};
        if (argc == 3)
        {
            auto const* tile = static_cast<dtNavMesh const&>(mesh).getTile(0);
            require(tile && tile->header, "Tile missing");
            int neighbor = -1;
            int samplePolygon = -1;
            for (int index = 0; index < tile->header->polyCount && neighbor < 0; ++index)
                for (unsigned char edge = 0; edge < tile->polys[index].vertCount; ++edge)
                    if (tile->polys[index].neis[edge])
                    {
                        samplePolygon = index;
                        neighbor = tile->polys[index].neis[edge] - 1;
                        break;
                    }
            require(neighbor >= 0, "No connected sample pair");
            start = center(*tile, samplePolygon);
            finish = center(*tile, neighbor);
        }
        else
        {
            start = {number(argv[2]), number(argv[4]), -number(argv[3])};
            finish = {number(argv[5]), number(argv[7]), -number(argv[6])};
        }
        std::array<float, 3> extents{0.75f, 0.6f, 0.75f}, snappedStart{}, snappedFinish{};
        dtPolyRef startRef = 0, finishRef = 0;
        require(complete(query.findNearestPoly(start.data(), extents.data(), &filter, &startRef, snappedStart.data())) && startRef,
            "Start outside mesh coverage");
        require(complete(query.findNearestPoly(finish.data(), extents.data(), &filter, &finishRef, snappedFinish.data())) && finishRef,
            "Destination outside mesh coverage");
        for (int axis = 0; axis < 3; ++axis)
            require(std::abs(start[axis] - snappedStart[axis]) <= extents[axis]
                && std::abs(finish[axis] - snappedFinish[axis]) <= extents[axis], "Endpoint snap too far");
        std::array<dtPolyRef, 4096> corridor{};
        int count = 0;
        require(complete(query.findPath(startRef, finishRef, snappedStart.data(), snappedFinish.data(), &filter,
            corridor.data(), &count, static_cast<int>(corridor.size()))) && count > 0 && corridor[count - 1] == finishRef,
            "No complete corridor");
        std::array<float, 4096 * 3> points{};
        std::array<unsigned char, 4096> flags{};
        int pointCount = 0;
        require(complete(query.findStraightPath(snappedStart.data(), snappedFinish.data(), corridor.data(), count,
            points.data(), flags.data(), nullptr, &pointCount, 4096, DT_STRAIGHTPATH_ALL_CROSSINGS))
            && pointCount > 0 && (flags[pointCount - 1] & DT_STRAIGHTPATH_END), "Incomplete straight path");
        std::cout << std::setprecision(9) << "{\"schema_version\":1,\"navigation_ready\":false,\"diagnostic\":true,\"build\":70888,\"complete\":true,\"polygons\":" << count;
        std::cout << ",\"start\":[" << start[0] << ',' << -start[2] << ',' << start[1] << "],\"end\":[" << finish[0] << ',' << -finish[2] << ',' << finish[1] << "],\"waypoints\":[";
        for (int index = 0; index < pointCount; ++index)
        {
            if (index) std::cout << ',';
            std::cout << '[' << points[index * 3] << ',' << -points[index * 3 + 2] << ',' << points[index * 3 + 1] << ']';
        }
        std::cout << "]}\n";
        return 0;
    }
    catch (std::exception const& error)
    {
        std::cerr << "Diagnostic query failed: " << error.what() << '\n';
        return 1;
    }
}
