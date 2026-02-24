#!/bin/bash
set -e

# Configuration
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEMINI_ROOT="$HOME/.gemini"
ANTIGRAVITY_ROOT="$GEMINI_ROOT/antigravity"

# Paths
LOCAL_RULES_DIR="$REPO_ROOT/.agent/rules"
LOCAL_SKILLS_DIR="$REPO_ROOT/.agent/skills"
LOCAL_WORKFLOWS_DIR="$REPO_ROOT/.agent/workflows"

GLOBAL_RULES_DIR="$GEMINI_ROOT/rules"
GLOBAL_SKILLS_DIR="$ANTIGRAVITY_ROOT/skills"
GLOBAL_WORKFLOWS_DIR="$ANTIGRAVITY_ROOT/global_workflows"
GLOBAL_GEMINI_MD="$GEMINI_ROOT/GEMINI.md"

echo "🚀 Deploying Agent Tools from $REPO_ROOT..."

# -----------------------------------------------------------------------------
# 1. Deploy Rules
# -----------------------------------------------------------------------------
echo "📜 Deploying Rules..."
mkdir -p "$GLOBAL_RULES_DIR"

# Symlink rule files
ln -sf "$LOCAL_RULES_DIR/rules.md" "$GLOBAL_RULES_DIR/rules.md"
ln -sf "$LOCAL_RULES_DIR/pr-standards.md" "$GLOBAL_RULES_DIR/pr-standards.md"

# Update global GEMINI.md while preserving memories
echo "   Updating $GLOBAL_GEMINI_MD..."

# Create temp file
TEMP_MD=$(mktemp)
trap 'rm -f "$TEMP_MD"' EXIT

# Write header and rule includes
cat <<EOF > "$TEMP_MD"
# Global Rules

@rules/rules.md
@rules/pr-standards.md

EOF

# Append existing memories if the file exists
if [ -f "$GLOBAL_GEMINI_MD" ]; then
    # Extract everything from "Gemini Added Memories" onwards
    MEMORIES=$(sed -n '/## Gemini Added Memories/,$p' "$GLOBAL_GEMINI_MD")
    
    if [ -n "$MEMORIES" ]; then
        echo "" >> "$TEMP_MD"
        printf "%s\n" "$MEMORIES" >> "$TEMP_MD"
    fi
fi

# Overwrite target file while preserving permissions and symlinks
cat "$TEMP_MD" > "$GLOBAL_GEMINI_MD"

# -----------------------------------------------------------------------------
# 2. Deploy Skills
# -----------------------------------------------------------------------------
echo "🧠 Deploying Skills..."
mkdir -p "$GLOBAL_SKILLS_DIR"

# Remove existing skill if it exists (to ensure clean symlink)
rm -rf "$GLOBAL_SKILLS_DIR/pr_review"

# Symlink the skill directory
ln -s "$LOCAL_SKILLS_DIR/pr_review" "$GLOBAL_SKILLS_DIR/pr_review"

# -----------------------------------------------------------------------------
# 3. Deploy Workflows
# -----------------------------------------------------------------------------
echo "🔄 Deploying Workflows..."
mkdir -p "$GLOBAL_WORKFLOWS_DIR"

# Remove existing workflow
rm -f "$GLOBAL_WORKFLOWS_DIR/pr-review-cycle.md"

# COPY the workflow file instead of symlinking
# This prevents the agent from resolving the symlink to the local .agent directory,
# which causes confusion when running in other projects.
cp "$LOCAL_WORKFLOWS_DIR/pr-review-cycle.md" "$GLOBAL_WORKFLOWS_DIR/pr-review-cycle.md"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo "✅ Deployment Complete!"
echo "   Rules:     Linked to $GLOBAL_RULES_DIR"
echo "   Skills:    Linked to $GLOBAL_SKILLS_DIR"
echo "   Workflows: Copied to $GLOBAL_WORKFLOWS_DIR"
echo ""
echo "👉 Please restart Antigravity IDE to reload global context."
