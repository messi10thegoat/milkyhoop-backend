table "atlas_schema_revisions" {
  schema = schema.atlas_schema_revisions
  column "version" {
    null = false
    type = character_varying
  }
  column "description" {
    null = false
    type = character_varying
  }
  column "type" {
    null    = false
    type    = bigint
    default = 2
  }
  column "applied" {
    null    = false
    type    = bigint
    default = 0
  }
  column "total" {
    null    = false
    type    = bigint
    default = 0
  }
  column "executed_at" {
    null = false
    type = timestamptz
  }
  column "execution_time" {
    null = false
    type = bigint
  }
  column "error" {
    null = true
    type = text
  }
  column "error_stmt" {
    null = true
    type = text
  }
  column "hash" {
    null = false
    type = character_varying
  }
  column "partial_hashes" {
    null = true
    type = jsonb
  }
  column "operator_version" {
    null = false
    type = character_varying
  }
  primary_key {
    columns = [column.version]
  }
}
table "audit_log_entries" {
  schema  = schema.auth
  comment = "Auth: Audit trail for user actions."
  column "instance_id" {
    null = true
    type = uuid
  }
  column "id" {
    null = false
    type = uuid
  }
  column "payload" {
    null = true
    type = json
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "ip_address" {
    null    = false
    type    = character_varying(64)
    default = ""
  }
  primary_key {
    columns = [column.id]
  }
  index "audit_logs_instance_id_idx" {
    columns = [column.instance_id]
  }
}
table "flow_state" {
  schema  = schema.auth
  comment = "stores metadata for pkce logins"
  column "id" {
    null = false
    type = uuid
  }
  column "user_id" {
    null = true
    type = uuid
  }
  column "auth_code" {
    null = false
    type = text
  }
  column "code_challenge_method" {
    null = false
    type = enum.code_challenge_method
  }
  column "code_challenge" {
    null = false
    type = text
  }
  column "provider_type" {
    null = false
    type = text
  }
  column "provider_access_token" {
    null = true
    type = text
  }
  column "provider_refresh_token" {
    null = true
    type = text
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "authentication_method" {
    null = false
    type = text
  }
  column "auth_code_issued_at" {
    null = true
    type = timestamptz
  }
  primary_key {
    columns = [column.id]
  }
  index "flow_state_created_at_idx" {
    on {
      desc   = true
      column = column.created_at
    }
  }
  index "idx_auth_code" {
    columns = [column.auth_code]
  }
  index "idx_user_id_auth_method" {
    columns = [column.user_id, column.authentication_method]
  }
}
table "identities" {
  schema  = schema.auth
  comment = "Auth: Stores identities associated to a user."
  column "provider_id" {
    null = false
    type = text
  }
  column "user_id" {
    null = false
    type = uuid
  }
  column "identity_data" {
    null = false
    type = jsonb
  }
  column "provider" {
    null = false
    type = text
  }
  column "last_sign_in_at" {
    null = true
    type = timestamptz
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "email" {
    null    = true
    type    = text
    comment = "Auth: Email is a generated column that references the optional email property in the identity_data"
    as {
      expr = "lower((identity_data ->> 'email'::text))"
      type = STORED
    }
  }
  column "id" {
    null    = false
    type    = uuid
    default = sql("gen_random_uuid()")
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "identities_user_id_fkey" {
    columns     = [column.user_id]
    ref_columns = [table.users.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "identities_email_idx" {
    comment = "Auth: Ensures indexed queries on the email column"
    on {
      column = column.email
      ops    = text_pattern_ops
    }
  }
  index "identities_user_id_idx" {
    columns = [column.user_id]
  }
  unique "identities_provider_id_provider_unique" {
    columns = [column.provider_id, column.provider]
  }
}
table "instances" {
  schema  = schema.auth
  comment = "Auth: Manages users across multiple sites."
  column "id" {
    null = false
    type = uuid
  }
  column "uuid" {
    null = true
    type = uuid
  }
  column "raw_base_config" {
    null = true
    type = text
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  primary_key {
    columns = [column.id]
  }
}
table "mfa_amr_claims" {
  schema  = schema.auth
  comment = "auth: stores authenticator method reference claims for multi factor authentication"
  column "session_id" {
    null = false
    type = uuid
  }
  column "created_at" {
    null = false
    type = timestamptz
  }
  column "updated_at" {
    null = false
    type = timestamptz
  }
  column "authentication_method" {
    null = false
    type = text
  }
  column "id" {
    null = false
    type = uuid
  }
  primary_key "amr_id_pk" {
    columns = [column.id]
  }
  foreign_key "mfa_amr_claims_session_id_fkey" {
    columns     = [column.session_id]
    ref_columns = [table.sessions.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  unique "mfa_amr_claims_session_id_authentication_method_pkey" {
    columns = [column.session_id, column.authentication_method]
  }
}
table "mfa_challenges" {
  schema  = schema.auth
  comment = "auth: stores metadata about challenge requests made"
  column "id" {
    null = false
    type = uuid
  }
  column "factor_id" {
    null = false
    type = uuid
  }
  column "created_at" {
    null = false
    type = timestamptz
  }
  column "verified_at" {
    null = true
    type = timestamptz
  }
  column "ip_address" {
    null = false
    type = inet
  }
  column "otp_code" {
    null = true
    type = text
  }
  column "web_authn_session_data" {
    null = true
    type = jsonb
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "mfa_challenges_auth_factor_id_fkey" {
    columns     = [column.factor_id]
    ref_columns = [table.mfa_factors.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "mfa_challenge_created_at_idx" {
    on {
      desc   = true
      column = column.created_at
    }
  }
}
table "mfa_factors" {
  schema  = schema.auth
  comment = "auth: stores metadata about factors"
  column "id" {
    null = false
    type = uuid
  }
  column "user_id" {
    null = false
    type = uuid
  }
  column "friendly_name" {
    null = true
    type = text
  }
  column "factor_type" {
    null = false
    type = enum.factor_type
  }
  column "status" {
    null = false
    type = enum.factor_status
  }
  column "created_at" {
    null = false
    type = timestamptz
  }
  column "updated_at" {
    null = false
    type = timestamptz
  }
  column "secret" {
    null = true
    type = text
  }
  column "phone" {
    null = true
    type = text
  }
  column "last_challenged_at" {
    null = true
    type = timestamptz
  }
  column "web_authn_credential" {
    null = true
    type = jsonb
  }
  column "web_authn_aaguid" {
    null = true
    type = uuid
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "mfa_factors_user_id_fkey" {
    columns     = [column.user_id]
    ref_columns = [table.users.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "factor_id_created_at_idx" {
    columns = [column.user_id, column.created_at]
  }
  index "mfa_factors_user_friendly_name_unique" {
    unique  = true
    columns = [column.friendly_name, column.user_id]
    where   = "(TRIM(BOTH FROM friendly_name) <> ''::text)"
  }
  index "mfa_factors_user_id_idx" {
    columns = [column.user_id]
  }
  index "unique_phone_factor_per_user" {
    unique  = true
    columns = [column.user_id, column.phone]
  }
  unique "mfa_factors_last_challenged_at_key" {
    columns = [column.last_challenged_at]
  }
}
table "one_time_tokens" {
  schema = schema.auth
  column "id" {
    null = false
    type = uuid
  }
  column "user_id" {
    null = false
    type = uuid
  }
  column "token_type" {
    null = false
    type = enum.one_time_token_type
  }
  column "token_hash" {
    null = false
    type = text
  }
  column "relates_to" {
    null = false
    type = text
  }
  column "created_at" {
    null    = false
    type    = timestamp
    default = sql("now()")
  }
  column "updated_at" {
    null    = false
    type    = timestamp
    default = sql("now()")
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "one_time_tokens_user_id_fkey" {
    columns     = [column.user_id]
    ref_columns = [table.users.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "one_time_tokens_relates_to_hash_idx" {
    columns = [column.relates_to]
    type    = HASH
  }
  index "one_time_tokens_token_hash_hash_idx" {
    columns = [column.token_hash]
    type    = HASH
  }
  index "one_time_tokens_user_id_token_type_key" {
    unique  = true
    columns = [column.user_id, column.token_type]
  }
  check "one_time_tokens_token_hash_check" {
    expr = "(char_length(token_hash) > 0)"
  }
}
table "refresh_tokens" {
  schema  = schema.auth
  comment = "Auth: Store of tokens used to refresh JWT tokens once they expire."
  column "instance_id" {
    null = true
    type = uuid
  }
  column "id" {
    null = false
    type = bigserial
  }
  column "token" {
    null = true
    type = character_varying(255)
  }
  column "user_id" {
    null = true
    type = character_varying(255)
  }
  column "revoked" {
    null = true
    type = boolean
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "parent" {
    null = true
    type = character_varying(255)
  }
  column "session_id" {
    null = true
    type = uuid
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "refresh_tokens_session_id_fkey" {
    columns     = [column.session_id]
    ref_columns = [table.sessions.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "refresh_tokens_instance_id_idx" {
    columns = [column.instance_id]
  }
  index "refresh_tokens_instance_id_user_id_idx" {
    columns = [column.instance_id, column.user_id]
  }
  index "refresh_tokens_parent_idx" {
    columns = [column.parent]
  }
  index "refresh_tokens_session_id_revoked_idx" {
    columns = [column.session_id, column.revoked]
  }
  index "refresh_tokens_updated_at_idx" {
    on {
      desc   = true
      column = column.updated_at
    }
  }
  unique "refresh_tokens_token_unique" {
    columns = [column.token]
  }
}
table "saml_providers" {
  schema  = schema.auth
  comment = "Auth: Manages SAML Identity Provider connections."
  column "id" {
    null = false
    type = uuid
  }
  column "sso_provider_id" {
    null = false
    type = uuid
  }
  column "entity_id" {
    null = false
    type = text
  }
  column "metadata_xml" {
    null = false
    type = text
  }
  column "metadata_url" {
    null = true
    type = text
  }
  column "attribute_mapping" {
    null = true
    type = jsonb
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "name_id_format" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "saml_providers_sso_provider_id_fkey" {
    columns     = [column.sso_provider_id]
    ref_columns = [table.sso_providers.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "saml_providers_sso_provider_id_idx" {
    columns = [column.sso_provider_id]
  }
  check "entity_id not empty" {
    expr = "(char_length(entity_id) > 0)"
  }
  check "metadata_url not empty" {
    expr = "((metadata_url = NULL::text) OR (char_length(metadata_url) > 0))"
  }
  check "metadata_xml not empty" {
    expr = "(char_length(metadata_xml) > 0)"
  }
  unique "saml_providers_entity_id_key" {
    columns = [column.entity_id]
  }
}
table "saml_relay_states" {
  schema  = schema.auth
  comment = "Auth: Contains SAML Relay State information for each Service Provider initiated login."
  column "id" {
    null = false
    type = uuid
  }
  column "sso_provider_id" {
    null = false
    type = uuid
  }
  column "request_id" {
    null = false
    type = text
  }
  column "for_email" {
    null = true
    type = text
  }
  column "redirect_to" {
    null = true
    type = text
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "flow_state_id" {
    null = true
    type = uuid
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "saml_relay_states_flow_state_id_fkey" {
    columns     = [column.flow_state_id]
    ref_columns = [table.flow_state.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  foreign_key "saml_relay_states_sso_provider_id_fkey" {
    columns     = [column.sso_provider_id]
    ref_columns = [table.sso_providers.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "saml_relay_states_created_at_idx" {
    on {
      desc   = true
      column = column.created_at
    }
  }
  index "saml_relay_states_for_email_idx" {
    columns = [column.for_email]
  }
  index "saml_relay_states_sso_provider_id_idx" {
    columns = [column.sso_provider_id]
  }
  check "request_id not empty" {
    expr = "(char_length(request_id) > 0)"
  }
}
table "auth" "schema_migrations" {
  schema  = schema.auth
  comment = "Auth: Manages updates to the auth system."
  column "version" {
    null = false
    type = character_varying(255)
  }
  primary_key {
    columns = [column.version]
  }
}
table "sessions" {
  schema  = schema.auth
  comment = "Auth: Stores session data associated to a user."
  column "id" {
    null = false
    type = uuid
  }
  column "user_id" {
    null = false
    type = uuid
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "factor_id" {
    null = true
    type = uuid
  }
  column "aal" {
    null = true
    type = enum.aal_level
  }
  column "not_after" {
    null    = true
    type    = timestamptz
    comment = "Auth: Not after is a nullable column that contains a timestamp after which the session should be regarded as expired."
  }
  column "refreshed_at" {
    null = true
    type = timestamp
  }
  column "user_agent" {
    null = true
    type = text
  }
  column "ip" {
    null = true
    type = inet
  }
  column "tag" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "sessions_user_id_fkey" {
    columns     = [column.user_id]
    ref_columns = [table.users.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "sessions_not_after_idx" {
    on {
      desc   = true
      column = column.not_after
    }
  }
  index "sessions_user_id_idx" {
    columns = [column.user_id]
  }
  index "user_id_created_at_idx" {
    columns = [column.user_id, column.created_at]
  }
}
table "sso_domains" {
  schema  = schema.auth
  comment = "Auth: Manages SSO email address domain mapping to an SSO Identity Provider."
  column "id" {
    null = false
    type = uuid
  }
  column "sso_provider_id" {
    null = false
    type = uuid
  }
  column "domain" {
    null = false
    type = text
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "sso_domains_sso_provider_id_fkey" {
    columns     = [column.sso_provider_id]
    ref_columns = [table.sso_providers.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
  index "sso_domains_domain_idx" {
    unique = true
    on {
      expr = "lower(domain)"
    }
  }
  index "sso_domains_sso_provider_id_idx" {
    columns = [column.sso_provider_id]
  }
  check "domain not empty" {
    expr = "(char_length(domain) > 0)"
  }
}
table "sso_providers" {
  schema  = schema.auth
  comment = "Auth: Manages SSO identity provider information; see saml_providers for SAML."
  column "id" {
    null = false
    type = uuid
  }
  column "resource_id" {
    null    = true
    type    = text
    comment = "Auth: Uniquely identifies a SSO provider according to a user-chosen resource ID (case insensitive), useful in infrastructure as code."
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  primary_key {
    columns = [column.id]
  }
  index "sso_providers_resource_id_idx" {
    unique = true
    on {
      expr = "lower(resource_id)"
    }
  }
  check "resource_id not empty" {
    expr = "((resource_id = NULL::text) OR (char_length(resource_id) > 0))"
  }
}
table "users" {
  schema  = schema.auth
  comment = "Auth: Stores user login data within a secure schema."
  column "instance_id" {
    null = true
    type = uuid
  }
  column "id" {
    null = false
    type = uuid
  }
  column "aud" {
    null = true
    type = character_varying(255)
  }
  column "role" {
    null = true
    type = character_varying(255)
  }
  column "email" {
    null = true
    type = character_varying(255)
  }
  column "encrypted_password" {
    null = true
    type = character_varying(255)
  }
  column "email_confirmed_at" {
    null = true
    type = timestamptz
  }
  column "invited_at" {
    null = true
    type = timestamptz
  }
  column "confirmation_token" {
    null = true
    type = character_varying(255)
  }
  column "confirmation_sent_at" {
    null = true
    type = timestamptz
  }
  column "recovery_token" {
    null = true
    type = character_varying(255)
  }
  column "recovery_sent_at" {
    null = true
    type = timestamptz
  }
  column "email_change_token_new" {
    null = true
    type = character_varying(255)
  }
  column "email_change" {
    null = true
    type = character_varying(255)
  }
  column "email_change_sent_at" {
    null = true
    type = timestamptz
  }
  column "last_sign_in_at" {
    null = true
    type = timestamptz
  }
  column "raw_app_meta_data" {
    null = true
    type = jsonb
  }
  column "raw_user_meta_data" {
    null = true
    type = jsonb
  }
  column "is_super_admin" {
    null = true
    type = boolean
  }
  column "created_at" {
    null = true
    type = timestamptz
  }
  column "updated_at" {
    null = true
    type = timestamptz
  }
  column "phone" {
    null    = true
    type    = text
    default = sql("NULL::character varying")
  }
  column "phone_confirmed_at" {
    null = true
    type = timestamptz
  }
  column "phone_change" {
    null    = true
    type    = text
    default = ""
  }
  column "phone_change_token" {
    null    = true
    type    = character_varying(255)
    default = ""
  }
  column "phone_change_sent_at" {
    null = true
    type = timestamptz
  }
  column "confirmed_at" {
    null = true
    type = timestamptz
    as {
      expr = "LEAST(email_confirmed_at, phone_confirmed_at)"
      type = STORED
    }
  }
  column "email_change_token_current" {
    null    = true
    type    = character_varying(255)
    default = ""
  }
  column "email_change_confirm_status" {
    null    = true
    type    = smallint
    default = 0
  }
  column "banned_until" {
    null = true
    type = timestamptz
  }
  column "reauthentication_token" {
    null    = true
    type    = character_varying(255)
    default = ""
  }
  column "reauthentication_sent_at" {
    null = true
    type = timestamptz
  }
  column "is_sso_user" {
    null    = false
    type    = boolean
    default = false
    comment = "Auth: Set this column to true when the account comes from SSO. These accounts can have duplicate emails."
  }
  column "deleted_at" {
    null = true
    type = timestamptz
  }
  column "is_anonymous" {
    null    = false
    type    = boolean
    default = false
  }
  primary_key {
    columns = [column.id]
  }
  index "confirmation_token_idx" {
    unique  = true
    columns = [column.confirmation_token]
    where   = "((confirmation_token)::text !~ '^[0-9 ]*$'::text)"
  }
  index "email_change_token_current_idx" {
    unique  = true
    columns = [column.email_change_token_current]
    where   = "((email_change_token_current)::text !~ '^[0-9 ]*$'::text)"
  }
  index "email_change_token_new_idx" {
    unique  = true
    columns = [column.email_change_token_new]
    where   = "((email_change_token_new)::text !~ '^[0-9 ]*$'::text)"
  }
  index "reauthentication_token_idx" {
    unique  = true
    columns = [column.reauthentication_token]
    where   = "((reauthentication_token)::text !~ '^[0-9 ]*$'::text)"
  }
  index "recovery_token_idx" {
    unique  = true
    columns = [column.recovery_token]
    where   = "((recovery_token)::text !~ '^[0-9 ]*$'::text)"
  }
  index "users_email_partial_key" {
    unique  = true
    columns = [column.email]
    comment = "Auth: A partial unique index that applies only when is_sso_user is false"
    where   = "(is_sso_user = false)"
  }
  index "users_instance_id_email_idx" {
    on {
      column = column.instance_id
    }
    on {
      expr = "lower((email)::text)"
    }
  }
  index "users_instance_id_idx" {
    columns = [column.instance_id]
  }
  index "users_is_anonymous_idx" {
    columns = [column.is_anonymous]
  }
  check "users_email_change_confirm_status_check" {
    expr = "((email_change_confirm_status >= 0) AND (email_change_confirm_status <= 2))"
  }
  unique "users_phone_key" {
    columns = [column.phone]
  }
}
table "Account" {
  schema = schema.public
  column "id" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "type" {
    null = false
    type = text
  }
  column "provider" {
    null = false
    type = text
  }
  column "providerAccountId" {
    null = false
    type = text
  }
  column "access_token" {
    null = true
    type = text
  }
  column "expires_at" {
    null = true
    type = integer
  }
  column "id_token" {
    null = true
    type = text
  }
  column "refresh_token" {
    null = true
    type = text
  }
  column "scope" {
    null = true
    type = text
  }
  column "session_state" {
    null = true
    type = text
  }
  column "token_type" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "Account_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  index "Account_provider_providerAccountId_key" {
    unique  = true
    columns = [column.provider, column.providerAccountId]
  }
}
table "Plans" {
  schema = schema.public
  column "planId" {
    null = false
    type = text
  }
  column "planName" {
    null = false
    type = text
  }
  primary_key {
    columns = [column.planId]
  }
}
table "Session" {
  schema = schema.public
  column "id" {
    null = false
    type = text
  }
  column "sessionToken" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "expires" {
    null = false
    type = timestamp(3)
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "Session_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  index "Session_sessionToken_key" {
    unique  = true
    columns = [column.sessionToken]
  }
}
table "User" {
  schema = schema.public
  column "id" {
    null = false
    type = text
  }
  column "email" {
    null = false
    type = text
  }
  column "username" {
    null = true
    type = text
  }
  column "name" {
    null = true
    type = text
  }
  column "fullname" {
    null = true
    type = text
  }
  column "nickname" {
    null = true
    type = text
  }
  column "avatarUrl" {
    null = true
    type = text
  }
  column "coverPhotoUrl" {
    null = true
    type = text
  }
  column "bio" {
    null = true
    type = text
  }
  column "emailVerified" {
    null = true
    type = timestamp(3)
  }
  column "createdAt" {
    null    = false
    type    = timestamp(3)
    default = sql("CURRENT_TIMESTAMP")
  }
  column "updatedAt" {
    null = false
    type = timestamp(3)
  }
  column "isVerified" {
    null    = false
    type    = boolean
    default = false
  }
  column "role" {
    null    = false
    type    = enum.Role
    default = "FREE"
  }
  column "lastInteraction" {
    null    = false
    type    = timestamp(3)
    default = sql("CURRENT_TIMESTAMP")
  }
  column "passwordHash" {
    null = true
    type = text
  }
  column "oauthProvider" {
    null = true
    type = text
  }
  column "oauthId" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  index "User_email_key" {
    unique  = true
    columns = [column.email]
  }
  index "User_username_key" {
    unique  = true
    columns = [column.username]
  }
}
table "UserAISettings" {
  schema = schema.public
  column "aiSettingsId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "botSlug" {
    null = false
    type = text
  }
  column "ragEnabled" {
    null    = false
    type    = boolean
    default = false
  }
  column "aiPersonalityProfile" {
    null = false
    type = jsonb
  }
  primary_key {
    columns = [column.aiSettingsId]
  }
  foreign_key "UserAISettings_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  index "UserAISettings_botSlug_key" {
    unique  = true
    columns = [column.botSlug]
  }
}
table "UserBusiness" {
  schema = schema.public
  column "businessId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "businessName" {
    null = false
    type = text
  }
  column "businessCategory" {
    null = false
    type = text
  }
  column "businessLicense" {
    null = false
    type = text
  }
  column "taxId" {
    null = false
    type = text
  }
  column "businessWebsite" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.businessId]
  }
  foreign_key "UserBusiness_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
}
table "UserFinance" {
  schema = schema.public
  column "financeId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "balance" {
    null    = false
    type    = double_precision
    default = 0
  }
  column "currency" {
    null = false
    type = text
  }
  column "paymentMethods" {
    null = false
    type = jsonb
  }
  column "loyaltyPoints" {
    null    = false
    type    = integer
    default = 0
  }
  primary_key {
    columns = [column.financeId]
  }
  foreign_key "UserFinance_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
}
table "UserLocations" {
  schema = schema.public
  column "locationId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "latitude" {
    null = false
    type = double_precision
  }
  column "longitude" {
    null = false
    type = double_precision
  }
  column "addressDetail" {
    null = false
    type = text
  }
  column "isPrimary" {
    null    = false
    type    = boolean
    default = false
  }
  primary_key {
    columns = [column.locationId]
  }
  foreign_key "UserLocations_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
}
table "UserMedia" {
  schema = schema.public
  column "mediaId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "mediaType" {
    null = false
    type = enum.MediaType
  }
  column "mediaUrl" {
    null = false
    type = text
  }
  column "uploadDate" {
    null    = false
    type    = timestamp(3)
    default = sql("CURRENT_TIMESTAMP")
  }
  primary_key {
    columns = [column.mediaId]
  }
  foreign_key "UserMedia_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
}
table "UserProfile" {
  schema = schema.public
  column "id" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "bio" {
    null = true
    type = text
  }
  column "phoneNumber" {
    null = true
    type = text
  }
  column "tagline" {
    null = true
    type = text
  }
  column "publicUrlSlug" {
    null = true
    type = text
  }
  column "digitalSignature" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "UserProfile_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  index "UserProfile_publicUrlSlug_key" {
    unique  = true
    columns = [column.publicUrlSlug]
  }
  index "UserProfile_userId_key" {
    unique  = true
    columns = [column.userId]
  }
}
table "UserSecurity" {
  schema = schema.public
  column "id" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "passwordHash" {
    null = true
    type = text
  }
  column "twoFactorEnabled" {
    null    = false
    type    = boolean
    default = false
  }
  column "oauthId" {
    null = true
    type = text
  }
  column "oauthProvider" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "UserSecurity_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  index "UserSecurity_userId_key" {
    unique  = true
    columns = [column.userId]
  }
}
table "UserSubscriptions" {
  schema = schema.public
  column "subscriptionId" {
    null = false
    type = text
  }
  column "userId" {
    null = false
    type = text
  }
  column "planId" {
    null = false
    type = text
  }
  column "tokenUsage" {
    null    = false
    type    = integer
    default = 0
  }
  column "tokenLimit" {
    null = false
    type = integer
  }
  column "tokenResetAt" {
    null = false
    type = timestamp(3)
  }
  column "subscriptionStart" {
    null = false
    type = timestamp(3)
  }
  column "subscriptionEnd" {
    null = false
    type = timestamp(3)
  }
  primary_key {
    columns = [column.subscriptionId]
  }
  foreign_key "UserSubscriptions_planId_fkey" {
    columns     = [column.planId]
    ref_columns = [table.Plans.column.planId]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
  foreign_key "UserSubscriptions_userId_fkey" {
    columns     = [column.userId]
    ref_columns = [table.User.column.id]
    on_update   = CASCADE
    on_delete   = CASCADE
  }
}
table "VerificationToken" {
  schema = schema.public
  column "identifier" {
    null = false
    type = text
  }
  column "token" {
    null = false
    type = text
  }
  column "expires" {
    null = false
    type = timestamp(3)
  }
  index "VerificationToken_identifier_token_key" {
    unique  = true
    columns = [column.identifier, column.token]
  }
  index "VerificationToken_token_key" {
    unique  = true
    columns = [column.token]
  }
}
table "_prisma_migrations" {
  schema = schema.public
  column "id" {
    null = false
    type = character_varying(36)
  }
  column "checksum" {
    null = false
    type = character_varying(64)
  }
  column "finished_at" {
    null = true
    type = timestamptz
  }
  column "migration_name" {
    null = false
    type = character_varying(255)
  }
  column "logs" {
    null = true
    type = text
  }
  column "rolled_back_at" {
    null = true
    type = timestamptz
  }
  column "started_at" {
    null    = false
    type    = timestamptz
    default = sql("now()")
  }
  column "applied_steps_count" {
    null    = false
    type    = integer
    default = 0
  }
  primary_key {
    columns = [column.id]
  }
}
table "public" "messages" {
  schema = schema.public
  column "id" {
    null = false
    type = serial
  }
  column "user_id" {
    null = false
    type = character_varying(255)
  }
  column "message" {
    null = false
    type = text
  }
  column "created_at" {
    null    = true
    type    = timestamp
    default = sql("CURRENT_TIMESTAMP")
  }
  primary_key {
    columns = [column.id]
  }
}
table "notifications" {
  schema = schema.public
  column "id" {
    null = false
    type = serial
  }
  column "user_id" {
    null = false
    type = uuid
  }
  column "message" {
    null = false
    type = text
  }
  column "type" {
    null = false
    type = text
  }
  column "status" {
    null = false
    type = text
  }
  column "created_at" {
    null    = true
    type    = timestamp
    default = sql("now()")
  }
  primary_key {
    columns = [column.id]
  }
}
table "realtime" "messages" {
  schema = schema.realtime
  column "topic" {
    null = false
    type = text
  }
  column "extension" {
    null = false
    type = text
  }
  column "payload" {
    null = true
    type = jsonb
  }
  column "event" {
    null = true
    type = text
  }
  column "private" {
    null    = true
    type    = boolean
    default = false
  }
  column "updated_at" {
    null    = false
    type    = timestamp
    default = sql("now()")
  }
  column "inserted_at" {
    null    = false
    type    = timestamp
    default = sql("now()")
  }
  column "id" {
    null    = false
    type    = uuid
    default = sql("gen_random_uuid()")
  }
  primary_key {
    columns = [column.id, column.inserted_at]
  }
  partition {
    type    = RANGE
    columns = [column.inserted_at]
  }
}
table "realtime" "schema_migrations" {
  schema = schema.realtime
  column "version" {
    null = false
    type = bigint
  }
  column "inserted_at" {
    null = true
    type = timestamp(0)
  }
  primary_key {
    columns = [column.version]
  }
}
table "subscription" {
  schema = schema.realtime
  column "id" {
    null = false
    type = bigint
    identity {
      generated = ALWAYS
    }
  }
  column "subscription_id" {
    null = false
    type = uuid
  }
  column "entity" {
    null = false
    type = regclass
  }
  column "filters" {
    null    = false
    type    = sql("realtime.user_defined_filter[]")
    default = "{}"
  }
  column "claims" {
    null = false
    type = jsonb
  }
  column "claims_role" {
    null = false
    type = regrole
    as {
      expr = "realtime.to_regrole((claims ->> 'role'::text))"
      type = STORED
    }
  }
  column "created_at" {
    null    = false
    type    = timestamp
    default = sql("timezone('utc'::text, now())")
  }
  primary_key "pk_subscription" {
    columns = [column.id]
  }
  index "ix_realtime_subscription_entity" {
    columns = [column.entity]
  }
  index "subscription_subscription_id_entity_filters_key" {
    unique  = true
    columns = [column.subscription_id, column.entity, column.filters]
  }
}
table "buckets" {
  schema = schema.storage
  column "id" {
    null = false
    type = text
  }
  column "name" {
    null = false
    type = text
  }
  column "owner" {
    null    = true
    type    = uuid
    comment = "Field is deprecated, use owner_id instead"
  }
  column "created_at" {
    null    = true
    type    = timestamptz
    default = sql("now()")
  }
  column "updated_at" {
    null    = true
    type    = timestamptz
    default = sql("now()")
  }
  column "public" {
    null    = true
    type    = boolean
    default = false
  }
  column "avif_autodetection" {
    null    = true
    type    = boolean
    default = false
  }
  column "file_size_limit" {
    null = true
    type = bigint
  }
  column "allowed_mime_types" {
    null = true
    type = sql("text[]")
  }
  column "owner_id" {
    null = true
    type = text
  }
  primary_key {
    columns = [column.id]
  }
  index "bname" {
    unique  = true
    columns = [column.name]
  }
}
table "migrations" {
  schema = schema.storage
  column "id" {
    null = false
    type = integer
  }
  column "name" {
    null = false
    type = character_varying(100)
  }
  column "hash" {
    null = false
    type = character_varying(40)
  }
  column "executed_at" {
    null    = true
    type    = timestamp
    default = sql("CURRENT_TIMESTAMP")
  }
  primary_key {
    columns = [column.id]
  }
  unique "migrations_name_key" {
    columns = [column.name]
  }
}
table "objects" {
  schema = schema.storage
  column "id" {
    null    = false
    type    = uuid
    default = sql("gen_random_uuid()")
  }
  column "bucket_id" {
    null = true
    type = text
  }
  column "name" {
    null = true
    type = text
  }
  column "owner" {
    null    = true
    type    = uuid
    comment = "Field is deprecated, use owner_id instead"
  }
  column "created_at" {
    null    = true
    type    = timestamptz
    default = sql("now()")
  }
  column "updated_at" {
    null    = true
    type    = timestamptz
    default = sql("now()")
  }
  column "last_accessed_at" {
    null    = true
    type    = timestamptz
    default = sql("now()")
  }
  column "metadata" {
    null = true
    type = jsonb
  }
  column "path_tokens" {
    null = true
    type = sql("text[]")
    as {
      expr = "string_to_array(name, '/'::text)"
      type = STORED
    }
  }
  column "version" {
    null = true
    type = text
  }
  column "owner_id" {
    null = true
    type = text
  }
  column "user_metadata" {
    null = true
    type = jsonb
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "objects_bucketId_fkey" {
    columns     = [column.bucket_id]
    ref_columns = [table.buckets.column.id]
    on_update   = NO_ACTION
    on_delete   = NO_ACTION
  }
  index "bucketid_objname" {
    unique  = true
    columns = [column.bucket_id, column.name]
  }
  index "idx_objects_bucket_id_name" {
    columns = [column.bucket_id, column.name]
  }
  index "name_prefix_search" {
    on {
      column = column.name
      ops    = text_pattern_ops
    }
  }
}
table "s3_multipart_uploads" {
  schema = schema.storage
  column "id" {
    null = false
    type = text
  }
  column "in_progress_size" {
    null    = false
    type    = bigint
    default = 0
  }
  column "upload_signature" {
    null = false
    type = text
  }
  column "bucket_id" {
    null = false
    type = text
  }
  column "key" {
    null    = false
    type    = text
    collate = "C"
  }
  column "version" {
    null = false
    type = text
  }
  column "owner_id" {
    null = true
    type = text
  }
  column "created_at" {
    null    = false
    type    = timestamptz
    default = sql("now()")
  }
  column "user_metadata" {
    null = true
    type = jsonb
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "s3_multipart_uploads_bucket_id_fkey" {
    columns     = [column.bucket_id]
    ref_columns = [table.buckets.column.id]
    on_update   = NO_ACTION
    on_delete   = NO_ACTION
  }
  index "idx_multipart_uploads_list" {
    columns = [column.bucket_id, column.key, column.created_at]
  }
}
table "s3_multipart_uploads_parts" {
  schema = schema.storage
  column "id" {
    null    = false
    type    = uuid
    default = sql("gen_random_uuid()")
  }
  column "upload_id" {
    null = false
    type = text
  }
  column "size" {
    null    = false
    type    = bigint
    default = 0
  }
  column "part_number" {
    null = false
    type = integer
  }
  column "bucket_id" {
    null = false
    type = text
  }
  column "key" {
    null    = false
    type    = text
    collate = "C"
  }
  column "etag" {
    null = false
    type = text
  }
  column "owner_id" {
    null = true
    type = text
  }
  column "version" {
    null = false
    type = text
  }
  column "created_at" {
    null    = false
    type    = timestamptz
    default = sql("now()")
  }
  primary_key {
    columns = [column.id]
  }
  foreign_key "s3_multipart_uploads_parts_bucket_id_fkey" {
    columns     = [column.bucket_id]
    ref_columns = [table.buckets.column.id]
    on_update   = NO_ACTION
    on_delete   = NO_ACTION
  }
  foreign_key "s3_multipart_uploads_parts_upload_id_fkey" {
    columns     = [column.upload_id]
    ref_columns = [table.s3_multipart_uploads.column.id]
    on_update   = NO_ACTION
    on_delete   = CASCADE
  }
}
enum "factor_type" {
  schema = schema.auth
  values = ["totp", "webauthn", "phone"]
}
enum "factor_status" {
  schema = schema.auth
  values = ["unverified", "verified"]
}
enum "aal_level" {
  schema = schema.auth
  values = ["aal1", "aal2", "aal3"]
}
enum "code_challenge_method" {
  schema = schema.auth
  values = ["s256", "plain"]
}
enum "one_time_token_type" {
  schema = schema.auth
  values = ["confirmation_token", "reauthentication_token", "recovery_token", "email_change_token_new", "email_change_token_current", "phone_change_token"]
}
enum "key_status" {
  schema = schema.pgsodium
  values = ["default", "valid", "invalid", "expired"]
}
enum "key_type" {
  schema = schema.pgsodium
  values = ["aead-ietf", "aead-det", "hmacsha512", "hmacsha256", "auth", "shorthash", "generichash", "kdf", "secretbox", "secretstream", "stream_xchacha20"]
}
enum "Role" {
  schema = schema.public
  values = ["FREE", "BUSINESS", "PRO", "CORPORATE", "ADMIN"]
}
enum "MediaType" {
  schema = schema.public
  values = ["IMAGE", "VIDEO"]
}
enum "equality_op" {
  schema = schema.realtime
  values = ["eq", "neq", "lt", "lte", "gt", "gte", "in"]
}
enum "action" {
  schema = schema.realtime
  values = ["INSERT", "UPDATE", "DELETE", "TRUNCATE", "ERROR"]
}
schema "atlas_schema_revisions" {
}
schema "auth" {
}
schema "extensions" {
}
schema "graphql" {
}
schema "graphql_public" {
}
schema "pgbouncer" {
}
schema "pgsodium" {
}
schema "public" {
}
schema "realtime" {
}
schema "storage" {
}
schema "vault" {
}
