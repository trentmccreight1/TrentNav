#include <CascLib.h>
#include <algorithm>
#include <array>
#include <cwctype>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    constexpr std::uint32_t expectedBuild = 70888;
    constexpr std::uint32_t maximumFileSize = 64 * 1024 * 1024;

    struct StorageCloser
    {
        void operator()(void* handle) const { CascCloseStorage(handle); }
    };

    struct FileCloser
    {
        void operator()(void* handle) const { CascCloseFile(handle); }
    };

    using Storage = std::unique_ptr<void, StorageCloser>;
    using File = std::unique_ptr<void, FileCloser>;

    [[noreturn]] void fail(std::string const& operation)
    {
        throw std::runtime_error(operation + " (CASC error " + std::to_string(GetCascError()) + ")");
    }

    std::uint32_t readInteger(std::vector<unsigned char> const& bytes, std::size_t offset)
    {
        if (offset > bytes.size() || bytes.size() - offset < sizeof(std::uint32_t))
            throw std::runtime_error("Truncated binary structure");
        std::uint32_t result;
        std::memcpy(&result, bytes.data() + offset, sizeof(result));
        return result;
    }

    std::vector<unsigned char> readFile(HANDLE storage, char const* identity, DWORD flags, std::uint32_t sizeLimit = maximumFileSize)
    {
        HANDLE rawFile = nullptr;
        if (!CascOpenFile(storage, identity, CASC_LOCALE_ENUS, flags | CASC_STRICT_DATA_CHECK, &rawFile))
            fail("Cannot open required local file");
        File file(rawFile);
        DWORD high = 0;
        DWORD size = CascGetFileSize(file.get(), &high);
        if (high != 0 || size == CASC_INVALID_SIZE || size == 0 || size > std::min(maximumFileSize, sizeLimit))
            throw std::runtime_error("Required file exceeds bounded probe size or has invalid length");
        std::vector<unsigned char> bytes(size);
        DWORD completed = 0;
        if (!CascReadFile(file.get(), bytes.data(), size, &completed) || completed != size)
        {
            DWORD error = GetCascError();
            ULONGLONG keyName = 0;
            std::ostringstream detail;
            detail << "Incomplete local file read (CASC error " << error << ")";
            if (error == ERROR_FILE_ENCRYPTED && CascGetNotFoundEncryptionKey(storage, &keyName))
                detail << "; missing asset-key identifier " << std::hex << std::uppercase << std::setw(16) << std::setfill('0') << keyName;
            throw std::runtime_error(detail.str());
        }
        return bytes;
    }

    void writeFile(std::filesystem::path const& path, std::vector<unsigned char> const& bytes)
    {
        if (std::filesystem::exists(path))
            throw std::runtime_error("Refusing to overwrite existing probe output: " + path.string());
        std::ofstream output(path, std::ios::binary);
        output.write(reinterpret_cast<char const*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
        output.close();
        if (!output)
            throw std::runtime_error("Failed to write complete probe output");
    }

    bool matches(std::vector<unsigned char> const& bytes, std::size_t offset, char const* tag)
    {
        if (bytes.size() - offset < 4)
            return false;
        return (bytes[offset] == static_cast<unsigned char>(tag[0]) &&
                bytes[offset + 1] == static_cast<unsigned char>(tag[1]) &&
                bytes[offset + 2] == static_cast<unsigned char>(tag[2]) &&
                bytes[offset + 3] == static_cast<unsigned char>(tag[3])) ||
               (bytes[offset] == static_cast<unsigned char>(tag[3]) &&
                bytes[offset + 1] == static_cast<unsigned char>(tag[2]) &&
                bytes[offset + 2] == static_cast<unsigned char>(tag[1]) &&
                bytes[offset + 3] == static_cast<unsigned char>(tag[0]));
    }

    std::size_t findChunk(std::vector<unsigned char> const& bytes, char const* tag, std::uint32_t minimumSize)
    {
        for (std::size_t offset = 0; offset < bytes.size();)
        {
            if (bytes.size() - offset < 8)
                throw std::runtime_error("Truncated chunk header");
            std::uint32_t size = readInteger(bytes, offset + 4);
            if (size > bytes.size() - offset - 8)
                throw std::runtime_error("Truncated chunk body");
            if (matches(bytes, offset, tag))
            {
                if (size < minimumSize)
                    throw std::runtime_error("Required chunk is too small");
                return offset + 8;
            }
            offset += 8 + size;
        }
        throw std::runtime_error(std::string("Required chunk missing: ") + tag);
    }

    std::string jsonString(std::string const& text)
    {
        std::ostringstream escaped;
        escaped << '"';
        for (unsigned char character : text)
        {
            if (character == '"' || character == '\\')
                escaped << '\\' << character;
            else if (character < 32 || character > 126)
                escaped << "\\u00" << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(character);
            else
                escaped << character;
        }
        return escaped.str() + '"';
    }

    struct Chunk
    {
        std::size_t offset;
        std::uint32_t size;
    };

    std::map<std::string, Chunk> dependencyChunks(std::vector<unsigned char> const& bytes)
    {
        std::map<std::string, Chunk> result;
        for (std::size_t offset = 0; offset < bytes.size();)
        {
            if (bytes.size() - offset < 8)
                throw std::runtime_error("Truncated dependency chunk header");
            auto size = readInteger(bytes, offset + 4);
            if (size > bytes.size() - offset - 8)
                throw std::runtime_error("Truncated dependency chunk");
            for (auto tag : {"MDDF", "MODF", "MMID", "MMDX", "MWID", "MWMO", "MOHD", "GFID", "MODI", "MODN", "MODD"})
                if (matches(bytes, offset, tag) && !result.emplace(tag, Chunk{offset + 8, size}).second)
                    throw std::runtime_error("Duplicate dependency chunk");
            offset += 8 + size;
        }
        return result;
    }

    struct Asset
    {
        std::string kind;
        std::uint32_t id;
        std::string name;
        std::string path;
        std::string error;
        std::size_t bytes = 0;
    };

    bool exportDependencies(HANDLE storage, std::filesystem::path const& output,
        std::array<std::vector<unsigned char>, 2> const& placements)
    {
        std::vector<Asset> assets;
        std::map<std::string, std::size_t> identities;
        std::vector<std::string> issues;
        auto add = [&](std::string const& kind, std::uint32_t id, std::string const& name)
        {
            if (id == 0 && name.empty())
                throw std::runtime_error("Zero dependency identity");
            std::string key = kind + ":" + (id ? std::to_string(id) : name);
            if (identities.contains(key))
                return;
            if (assets.size() >= 4096)
                throw std::runtime_error("Dependency count exceeds 4096");
            identities.emplace(key, assets.size());
            assets.push_back({kind, id, name, "assets/" + std::to_string(assets.size()) + ".bin", "", 0});
        };
        auto nameAt = [](std::vector<unsigned char> const& bytes, Chunk strings, std::uint32_t offset)
        {
            if (offset >= strings.size)
                throw std::runtime_error("Dependency string offset out of bounds");
            std::string name;
            for (std::size_t index = offset; index < strings.size && name.size() <= 1024; ++index)
            {
                unsigned char character = bytes[strings.offset + index];
                if (!character)
                {
                    if (name.empty())
                        throw std::runtime_error("Empty dependency name");
                    return name;
                }
                if (character < 32 || character > 126)
                    throw std::runtime_error("Non-ASCII dependency name");
                name += static_cast<char>(character);
            }
            throw std::runtime_error("Unterminated or overlong dependency name");
        };
        for (auto const& placement : placements)
        {
            if (placement.empty())
                continue;
            auto chunks = dependencyChunks(placement);
            for (auto tag : {"MDDF", "MODF"})
            {
                if (!chunks.contains(tag))
                    continue;
                bool isM2 = std::strcmp(tag, "MDDF") == 0;
                auto chunk = chunks.at(tag);
                std::uint32_t stride = isM2 ? 36 : 64;
                if (chunk.size % stride || chunk.size / stride > 100000)
                    throw std::runtime_error("Invalid placement record count");
                for (std::size_t offset = chunk.offset; offset < chunk.offset + chunk.size; offset += stride)
                {
                    auto identity = readInteger(placement, offset);
                    std::uint16_t flags;
                    std::memcpy(&flags, placement.data() + offset + (isM2 ? 34 : 56), 2);
                    if (flags & (isM2 ? 0x40 : 0x8))
                        add(isM2 ? "m2" : "wmo", identity, "");
                    else
                    {
                        auto offsets = chunks.at(isM2 ? "MMID" : "MWID");
                        if (offsets.size % 4 || identity >= offsets.size / 4)
                            throw std::runtime_error("Placement name index out of bounds");
                        auto nameOffset = readInteger(placement, offsets.offset + identity * 4);
                        auto name = nameAt(placement, chunks.at(isM2 ? "MMDX" : "MWMO"), nameOffset);
                        if (isM2 && name.size() >= 4 && (name.ends_with(".mdx") || name.ends_with(".MDX") || name.ends_with(".mdl") || name.ends_with(".MDL")))
                            name.replace(name.size() - 4, 4, ".m2");
                        add(isM2 ? "m2" : "wmo", 0, name);
                    }
                }
            }
        }
        std::filesystem::create_directory(output / "assets");
        std::uint64_t totalBytes = 0;
        for (std::size_t index = 0; index < assets.size(); ++index)
        {
            Asset asset = assets[index];
            try
            {
                auto remaining = static_cast<std::uint32_t>(512ull * 1024 * 1024 - totalBytes);
                auto bytes = asset.id ? readFile(storage, CASC_FILE_DATA_ID(asset.id), CASC_OPEN_BY_FILEID, remaining) :
                    readFile(storage, asset.name.c_str(), CASC_OPEN_BY_NAME, remaining);
                totalBytes += bytes.size();
                writeFile(output / asset.path, bytes);
                asset.bytes = bytes.size();
                if (asset.kind == "wmo")
                {
                    auto chunks = dependencyChunks(bytes);
                    auto header = chunks.at("MOHD");
                    if (header.size < 64)
                        throw std::runtime_error("Short WMO header");
                    auto groupCount = readInteger(bytes, header.offset + 4);
                    if (groupCount > 1024)
                        throw std::runtime_error("WMO exceeds 1024 groups");
                    if (groupCount)
                    {
                        auto groups = chunks.at("GFID");
                        if (groups.size % 4 || groups.size / 4 < groupCount)
                            throw std::runtime_error("Missing base-LOD group identities");
                        for (std::uint32_t group = 0; group < groupCount; ++group)
                            add("wmo_group", readInteger(bytes, groups.offset + group * 4), "");
                    }
                    if (chunks.contains("MODI"))
                    {
                        auto doodads = chunks.at("MODI");
                        if (doodads.size % 4)
                            throw std::runtime_error("Invalid MODI array");
                        if (chunks.contains("MODD"))
                        {
                            auto records = chunks.at("MODD");
                            if (records.size % 40 || records.size / 40 > 100000)
                                throw std::runtime_error("Invalid WMO doodad record count");
                            for (std::size_t offset = records.offset; offset < records.offset + records.size; offset += 40)
                            {
                                auto nameIndex = readInteger(bytes, offset) & 0xFFFFFF;
                                if (nameIndex >= doodads.size / 4 || !readInteger(bytes, doodads.offset + nameIndex * 4))
                                    throw std::runtime_error("WMO doodad references missing model identity");
                            }
                        }
                        for (std::size_t offset = doodads.offset; offset < doodads.offset + doodads.size; offset += 4)
                            if (auto id = readInteger(bytes, offset))
                                add("m2", id, "");
                    }
                    else if ((chunks.contains("MODN") && chunks.at("MODN").size) ||
                        (chunks.contains("MODD") && chunks.at("MODD").size))
                        issues.push_back("WMO named doodad dependencies unsupported: " + asset.path);
                }
            }
            catch (std::exception const& error)
            {
                asset.error = error.what();
            }
            assets[index] = asset;
            if (totalBytes == 512ull * 1024 * 1024 && index + 1 < assets.size())
            {
                issues.push_back("Dependency byte budget exhausted; queued assets not read");
                break;
            }
        }
        std::ostringstream manifest;
        manifest << "{\"schema_version\":1,\"build\":70888,\"navigation_ready\":false,\"assets\":[";
        std::size_t missing = 0;
        for (std::size_t index = 0; index < assets.size(); ++index)
        {
            auto const& asset = assets[index];
            if (index) manifest << ',';
            if (!asset.bytes || !asset.error.empty()) ++missing;
            manifest << "{\"kind\":" << jsonString(asset.kind) << ",\"file_data_id\":" << asset.id
                << ",\"name\":" << jsonString(asset.name) << ",\"path\":" << jsonString(asset.path)
                << ",\"size_bytes\":" << asset.bytes << ",\"error\":" << jsonString(asset.error) << '}';
        }
        manifest << "],\"issues\":[";
        for (std::size_t index = 0; index < issues.size(); ++index)
        {
            if (index) manifest << ',';
            manifest << jsonString(issues[index]);
        }
        manifest << "],\"dependencies_complete\":" << ((!missing && issues.empty()) ? "true" : "false") << "}";
        auto text = manifest.str();
        writeFile(output / "collision-assets.json", std::vector<unsigned char>(text.begin(), text.end()));
        std::cout << "dependency_assets=" << assets.size() << "\ndependency_failures=" << missing
            << "\ndependency_issues=" << issues.size() << "\ncollision_complete=false\nnavigation_ready=false\n";
        return !missing && issues.empty();
    }

    std::uint32_t parseTile(char const* text)
    {
        std::string input(text);
        std::size_t consumed = 0;
        unsigned long value = std::stoul(input, &consumed);
        if (consumed != input.size() || value >= 64)
            throw std::runtime_error("Tile coordinates must be integers from 0 through 63");
        return static_cast<std::uint32_t>(value);
    }
}

int main(int argc, char** arguments)
{
    try
    {
        if (argc < 3)
            throw std::runtime_error("Usage: trentnav_client_probe <installation-root> <new-output-directory> [--keys local-file] [wdt-name tile-x tile-y]");
        int nextArgument = 3;
        std::filesystem::path keysPath;
        if (argc > 3 && std::strcmp(arguments[3], "--keys") == 0)
        {
            if (argc < 5)
                throw std::runtime_error("--keys requires a local file");
            keysPath = arguments[4];
            nextArgument = 5;
        }
        if (argc != nextArgument && argc != nextArgument + 3)
            throw std::runtime_error("Expected either no WDT selection or a WDT name and two tile coordinates");
        bool hasTile = argc == nextArgument + 3;
        bool dependenciesComplete = true;
        std::uint32_t tileX = hasTile ? parseTile(arguments[nextArgument + 1]) : 0;
        std::uint32_t tileY = hasTile ? parseTile(arguments[nextArgument + 2]) : 0;
        std::filesystem::path root = std::filesystem::canonical(arguments[1]);
        std::filesystem::path data = root / "Data";
        if (!std::filesystem::is_directory(data) || !std::filesystem::is_regular_file(root / ".build.info"))
            throw std::runtime_error("Input must be the parent installation containing Data and .build.info");
        std::filesystem::path output = std::filesystem::absolute(arguments[2]).lexically_normal();
        std::filesystem::path resolvedOutput = std::filesystem::weakly_canonical(output);
        auto rootText = root.wstring();
        auto outputText = resolvedOutput.wstring();
        auto equalIgnoringCase = [](wchar_t left, wchar_t right) { return towlower(left) == towlower(right); };
        if (outputText.size() >= rootText.size() &&
            std::equal(rootText.begin(), rootText.end(), outputText.begin(), equalIgnoringCase) &&
            (outputText.size() == rootText.size() || outputText[rootText.size()] == L'\\'))
            throw std::runtime_error("Output must be outside the game installation");
        if (std::filesystem::exists(output))
            throw std::runtime_error("Output directory must not exist; each probe is a separate receipt");

#ifdef UNICODE
        auto dataText = data.wstring();
        wchar_t const* productName = L"wowf";
#else
        auto dataText = data.string();
        char const* productName = "wowf";
#endif
        CASC_OPEN_STORAGE_ARGS options = {};
        options.Size = sizeof(options);
        options.szLocalPath = dataText.c_str();
        options.szCodeName = productName;
        options.dwLocaleMask = CASC_LOCALE_ENUS;
        HANDLE rawStorage = nullptr;
        if (!CascOpenStorageEx(nullptr, &options, false, &rawStorage))
            fail("Cannot open offline wowf storage");
        Storage storage(rawStorage);
        DWORD features = 0;
        if (!CascGetStorageInfo(storage.get(), CascStorageFeatures, &features, sizeof(features), nullptr))
            fail("Cannot verify storage features");
        if ((features & CASC_FEATURE_ONLINE) != 0)
            throw std::runtime_error("Online storage is forbidden");
        CASC_STORAGE_PRODUCT product = {};
        if (!CascGetStorageInfo(storage.get(), CascStorageProduct, &product, sizeof(product), nullptr))
            fail("Cannot verify product/build");
        if (product.BuildNumber != expectedBuild || std::strcmp(product.szCodeName, "wowf") != 0)
            throw std::runtime_error("Storage does not match pinned wowf build 70888");
        if (!keysPath.empty())
        {
            auto keySize = std::filesystem::file_size(keysPath);
            if (keySize == 0 || keySize > 2 * 1024 * 1024)
                throw std::runtime_error("Local asset-key list exceeds size limit");
            std::ifstream keyInput(keysPath, std::ios::binary);
            std::string keys(static_cast<std::size_t>(keySize), '\0');
            keyInput.read(keys.data(), static_cast<std::streamsize>(keys.size()));
            if (!keyInput || keys.find('\0') != std::string::npos)
                throw std::runtime_error("Local asset-key list could not be read as complete text");
            if (!CascImportKeysFromString(storage.get(), keys.c_str()))
                fail("Cannot import local asset keys");
        }

        std::cout << "product=wowf\nbuild=" << product.BuildNumber << "\nonline=false\n";
        if (!hasTile)
        {
            auto map = readFile(storage.get(), CASC_FILE_DATA_ID(1349477), CASC_OPEN_BY_FILEID);
            if (map.size() < 80 || std::memcmp(map.data(), "WDC5", 4) != 0)
                throw std::runtime_error("Unsupported Map.db2 header; no speculative parsing");
            std::filesystem::create_directories(output);
            writeFile(output / "Map.db2", map);
            std::cout << "map_db2_bytes=" << map.size() << "\n";
        }

        if (hasTile)
        {
            std::string wdtIdentity(arguments[nextArgument]);
            std::vector<unsigned char> wdt;
            if (wdtIdentity.starts_with("id:"))
            {
                std::size_t consumed = 0;
                unsigned long fileId = std::stoul(wdtIdentity.substr(3), &consumed);
                if (consumed != wdtIdentity.size() - 3 || fileId == 0 || fileId > std::numeric_limits<std::uint32_t>::max())
                    throw std::runtime_error("Invalid explicit WDT file-data ID");
                wdt = readFile(storage.get(), CASC_FILE_DATA_ID(fileId), CASC_OPEN_BY_FILEID);
                std::cout << "wdt_file_id=" << fileId << "\n";
            }
            else
                wdt = readFile(storage.get(), wdtIdentity.c_str(), CASC_OPEN_BY_NAME);
            std::size_t version = findChunk(wdt, "MVER", 4);
            if (readInteger(wdt, version) != 18)
                throw std::runtime_error("Unsupported WDT version");
            std::size_t header = findChunk(wdt, "MPHD", 4);
            if ((readInteger(wdt, header) & 0x200) == 0)
                throw std::runtime_error("Selected WDT does not declare modern file-data IDs");
            std::size_t main = findChunk(wdt, "MAIN", 64 * 64 * 8);
            if ((readInteger(wdt, main + (tileY * 64 + tileX) * 8) & 1) == 0)
                throw std::runtime_error("Selected WDT tile is not declared present");
            std::size_t maid = findChunk(wdt, "MAID", 64 * 64 * 32);
            std::size_t tile = maid + (tileY * 64 + tileX) * 32;
            std::uint32_t rootId = readInteger(wdt, tile);
            if (rootId == 0)
                throw std::runtime_error("Selected WDT tile has no root ADT");
            auto adt = readFile(storage.get(), CASC_FILE_DATA_ID(rootId), CASC_OPEN_BY_FILEID);
            if (readInteger(adt, findChunk(adt, "MVER", 4)) != 18)
                throw std::runtime_error("Unsupported root ADT version");
            findChunk(adt, "MCNK", 128);
            std::array<std::vector<unsigned char>, 2> placements;
            for (std::size_t index = 0; index < placements.size(); ++index)
            {
                std::uint32_t placementId = readInteger(wdt, tile + (index + 1) * 4);
                std::cout << "object_adt_" << index << "_id=" << placementId << "\n";
                if (placementId != 0)
                {
                    placements[index] = readFile(storage.get(), CASC_FILE_DATA_ID(placementId), CASC_OPEN_BY_FILEID);
                    std::size_t placementVersion = findChunk(placements[index], "MVER", 4);
                    if (readInteger(placements[index], placementVersion) != 18)
                        throw std::runtime_error("Unsupported placement ADT version");
                }
            }
            std::filesystem::create_directories(output);
            writeFile(output / "world.wdt", wdt);
            writeFile(output / "root.adt", adt);
            for (std::size_t index = 0; index < placements.size(); ++index)
                if (!placements[index].empty())
                    writeFile(output / ("objects" + std::to_string(index) + ".adt"), placements[index]);
            std::cout << "tile_x=" << tileX << "\ntile_y=" << tileY << "\nroot_adt_id=" << rootId << "\nroot_adt_bytes=" << adt.size() << "\n";
            dependenciesComplete = exportDependencies(storage.get(), output, placements);
        }
        std::cout << (dependenciesComplete ? "status=asset_probe_complete\n" : "status=asset_probe_incomplete\n");
        return 0;
    }
    catch (std::exception const& error)
    {
        std::cerr << "Probe failed: " << error.what() << "\n";
        return 1;
    }
}
