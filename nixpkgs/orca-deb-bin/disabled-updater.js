"use strict";

/**
 * Provides the Nix package's immutable replacement for electron-updater.
 *
 * Orca's UI expects an EventEmitter-shaped updater, but a Nix store path must
 * never download an artifact or invoke a package manager. This object keeps
 * the expected status events while making all update and install operations
 * deterministic no-ops.
 */

const { EventEmitter } = require("node:events");

class NixDisabledUpdater extends EventEmitter {
  constructor() {
    super();
    this.autoDownload = false;
    this.autoInstallOnAppQuit = false;
    this.autoRunAppAfterInstall = false;
    this.allowDowngrade = false;
    this.allowPrerelease = false;
    this.disableDifferentialDownload = true;
    this.logger = null;
  }

  /**
   * Accepts Orca's feed configuration without contacting the configured URL.
   *
   * @returns {void}
   */
  setFeedURL() {}

  /**
   * Reports that no update is available without network or filesystem I/O.
   *
   * @returns {Promise<{isUpdateAvailable: boolean, updateInfo: object, cancellationToken: null}>}
   */
  checkForUpdates() {
    queueMicrotask(() => {
      this.emit("checking-for-update");
      this.emit("update-not-available");
    });
    return Promise.resolve({
      isUpdateAvailable: false,
      updateInfo: { version: "0.0.0-nix-disabled" },
      cancellationToken: null,
    });
  }

  /**
   * Rejects an explicit download request instead of writing a cache artifact.
   *
   * @returns {Promise<never>}
   */
  downloadUpdate() {
    return Promise.reject(new Error("Orca updates are managed by Nix"));
  }

  /**
   * Rejects an explicit installation request instead of mutating the system.
   *
   * @returns {never}
   */
  quitAndInstall() {
    throw new Error("Orca updates are managed by Nix");
  }

  /**
   * Accepts compatibility calls without retaining credentials.
   *
   * @returns {void}
   */
  addAuthHeader() {}
}

exports.disabledAutoUpdater = new NixDisabledUpdater();
