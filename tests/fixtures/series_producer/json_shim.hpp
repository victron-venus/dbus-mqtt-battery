// Minimal host adapter for executing the producer's unchanged JSON publication
// lambda. It tests producer field values, not ArduinoJson's wire formatting.
#pragma once
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <type_traits>
#include <vector>

struct JsonNode {
  enum Kind { Null, Scalar, String, Object, Array } kind{Null};
  std::string scalar;
  std::map<std::string, std::shared_ptr<JsonNode>> object;
  std::vector<std::shared_ptr<JsonNode>> array;
};

inline void write_json(std::ostream &out, const JsonNode &node) {
  if (node.kind == JsonNode::Null) { out << "null"; return; }
  if (node.kind == JsonNode::Scalar) { out << node.scalar; return; }
  if (node.kind == JsonNode::String) { out << std::quoted(node.scalar); return; }
  bool first = true;
  out << (node.kind == JsonNode::Object ? '{' : '[');
  if (node.kind == JsonNode::Object) {
    for (const auto &item : node.object) {
      if (!first) out << ',';
      first = false;
      out << std::quoted(item.first) << ':';
      write_json(out, *item.second);
    }
  } else {
    for (const auto &item : node.array) {
      if (!first) out << ',';
      first = false;
      write_json(out, *item);
    }
  }
  out << (node.kind == JsonNode::Object ? '}' : ']');
}

struct JsonValue {
  std::shared_ptr<JsonNode> node;
  explicit JsonValue(std::shared_ptr<JsonNode> value) : node(std::move(value)) {}
  JsonValue &operator=(std::nullptr_t) { node->kind = JsonNode::Null; return *this; }
  JsonValue &operator=(const char *value) {
    node->kind = JsonNode::String;
    node->scalar = value;
    return *this;
  }
  template<typename T, typename = std::enable_if_t<std::is_arithmetic_v<T>>>
  JsonValue &operator=(T value) {
    std::ostringstream out;
    out << std::boolalpha << std::setprecision(9) << value;
    node->kind = JsonNode::Scalar;
    node->scalar = out.str();
    return *this;
  }
  template<typename T> T to() { return T(node); }
};

struct JsonObject {
  std::shared_ptr<JsonNode> node;
  JsonObject() : JsonObject(std::make_shared<JsonNode>()) {}
  explicit JsonObject(std::shared_ptr<JsonNode> value) : node(std::move(value)) {
    node->kind = JsonNode::Object;
  }
  JsonValue operator[](const char *key) {
    auto &value = node->object[key];
    if (!value) value = std::make_shared<JsonNode>();
    return JsonValue(value);
  }
};

struct JsonArray {
  std::shared_ptr<JsonNode> node;
  explicit JsonArray(std::shared_ptr<JsonNode> value) : node(std::move(value)) {
    node->kind = JsonNode::Array;
  }
  template<typename T> T add() {
    auto value = std::make_shared<JsonNode>();
    node->array.push_back(value);
    return T(value);
  }
  template<typename T> void add(T value) {
    auto child = std::make_shared<JsonNode>();
    JsonValue{child} = value;
    node->array.push_back(child);
  }
};
