---
created: 2026-05-29
updated: 2026-06-05
---
# Data
The following section documents design decisions on the DuckDB based, database that the application will be using.

## Tables
### source_registry
- Each source that a user registers, gets an entry into this table.
- The source_registry table knows about the actual instance table (report), but the instance table should not have knowledge about the registry table.
- If a primary key can't be determined or pulled, we should assume that the first column is the primary key. This should only be the case when the primary key is invalid or missing
- This table is mainly used to create the tables that the user ends up uploading as part of their workflow. This acts as a registry, so that the user can also see what sources they have uploaded.

| field name         | type     | description                                                                                                                                                                                                                                                                                                                                                          |
| ------------------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_id`        | UUID     | the primary key for this table. A UUID created from `source_name`, `primary_key`, and `ingestion_method`.                                                                                                                                                                                                                                                            |
| `source_name`      | string   | name of the report, used in the table creation and subsequent uploads                                                                                                                                                                                                                                                                                                |
| `date_ingested`    | datetime | used as part of the date filters, rollbacks, etc.                                                                                                                                                                                                                                                                                                                    |
| `date_registered`  | date     | used to save the first time the report was registered. This helps to keep track of the age of a report, whether we need a new report, or when we stopped using a report.                                                                                                                                                                                             |
| `ingestion_method` | enum     | used to determine how we should treat each new record during ingestion, when there are duplicate ids. To start, we have `upsert`, `update`, and `skip`.                                                                                                                                                                                                              |
| `pattern`          | string   | possible naming conventions that the report may have if the application were to search in the directory for the report. Also open to having this be a regex string                                                                                                                                                                                                   |
| `primary_key`      | string   | the primary key column, that the instance table uses. Used during table creation and joins with other reports. For example, if the user registers Foo as a source, when the actual data is uploaded to that table, then the backend will need to know which column is the primary key. Another example would be during table creation, and defining the primary_key. |
| `table_url`        | string   | the url to where the actual table with the data uploaded (with the columns and column types are registered) is stored. This url is used for look ups and writes into the various tables.                                                                                                                                                                             |


### function_registry
- the function registry is used to register and store functions that the user has uploaded to the app, so that the functions can be used as checks, transformations, or deliverables.

| field name             | type   | description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `function_id`          | UUID   | created from `function_name`, `function_signature`, and `return_type`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `function_signature`   | enum   | whether the function has a `scalar`, `column_backed`, `pd.series`, `pd.dataframe` signature. This is determined during function registry and validation, where the backend will determine the signature by the least granular parameter. This field helps to determine whether a function is `multi_select_eligible`, and can run multiple columns in a parameter for alias_maps and running the function multiple times in a single report.                                                                                                                                                                                                                                                                                                                        |
| `function_name`        | string | name of the function, usually used in summary sheets, quick lookups from other sources, and lookups to get the UUID of the function. Pulled from `__name__`, so that the function name that the user gives is the name of the function that gets referenced                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `function_doc`         | string | this is pulled from the function docstring, and is used as a tooltip to the user, using the app, to help remind them what the function actually does. The tooltip should also show the user parameters and parameter types in the function, which can be pulled via `inspect.signature` or something similar                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `function_return_type` | enum   | the return type of the function, used to determine how the results should be delivered. For example, if a function takes in scalar parameters (int, float, str) and returns a scalar, we know that the function is being looped, where each record is getting passed as the argument. In order to fully validate a table, and return the results, if a function returns a scalar, we essentially need to store each row's result, and return the results when all records have been run. For a pd.Series return, since these run as vectors, we can essentially just return it as a pd.Series. Practically, in the backend, these will all be wrapped in some kind of object, so that another object using it, won't have to know which return type a function had. |
| `function_type`        | enum   | whether the function is a `validation` function, where the point of the function is to check whether a column or the table passes a set of checks, or a `transform` function, where the function aims to change the values in the dataset. This is determined by a function that takes in the `function_signature`, `function_return_type` and checks whether the function returns a `boolean` \| `pd.Series[bool]`, which makes it a validation, or returns non-boolean data types (transform).                                                                                                                                                                                                                                                                    |
| `module_path`          | string | the path to where the actual function lives in the app, so that the function can be used and pulled. The idea is that this table gets called, with the functions that are tied to the report, and we pull the actual python function to run through this module_path field.<br>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

### column_registry
- we're going to assume if a `column_name` and `column_type` are the same between different reports, then they are treated as essentially the same column when it comes to aliases and table creation. Meaning, if the column `Foo: str` is in Table A and Table B, for alias_map and table creation purposes, Foo is the same in both. This is important when we use alias_map
- Used when inferring a new source's data type for a specific column

| field name    | type   | description                                                                                                                        |
| ------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `column_id`   | UUID   | UUID created from the `column_name`, `column_type`                                                                                 |
| `column_name` | string | the name of the column in the report. Taken directly from the spreadsheet.                                                         |
| `column_type` | enum   | used to determine the column data type for validation and table creation. Note that mismatches will cause errors during ingestion. |

### parameter
- this table is not a registry, in that a parameter with the different names can be the same parameter, and a parameter with the same name can be a different parameter (think `x` as the name of a parameter).

| field name    | type   | description                                                                                                                                       |
| ------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `param_id`    | UUID   | uses `param_name`, `function_id`, `paramm_type` to generate the UUID.                                                                             |
| `param_name`  | string | name of the parameter inside of the function definition                                                                                           |
| `param_type`  | enum   | the parameter data type. This is typed by the user when they define the definition in a python module.                                            |
| `function_id` | UUID   | the id field from the `function_registry`. This is used to pull all parameters that are under that function, so all parameters are accounted for. |

## Relational Tables
- These are tables we need to handle the tables with many to many relationships.
- The naming convention for these types of relational tables will be `_map` in our code. For example, `func_foo_map`.

### source_column_map
- this relational map is used to get the columns under a single report, or to get all the reports that uses a single column

| field name             | type | description                                              |
| ---------------------- | ---- | -------------------------------------------------------- |
| `source_column_map_id` | UUID | the UUID created using the `column_id` and `source_id`   |
| `column_id`            | UUID | the `column_id` key tied to the `column_registry` table. |
| `source_id`            | UUID | the `source_id` key tied to the `source_registry` table. |

### source_function_map
- this relational map is used to help the backend get a list of functions that are needed that is tied to a given report. This would be a request when we want to validate the data with the user created functions and we need to determine which functions to run
- this map is also used when the user wants to edit a function, and wants to see all the source files that will be affected by the change

| field name               | type | description                                   |
| ------------------------ | ---- | --------------------------------------------- |
| `source_function_map_id` | UUID | UUID using the `source_id` and `function_id`. |
| `source_id`              | UUID | id from the `source_registry`                 |
| `function_id`            | UUID | id from the `function_registry`               |

### alias_map
- this is likely the most important table in the entire app, as it allows the user to run a single function, on multiple columns, on a single report. It also allows the user to be able to reuse parameters and their column mapping across multiple reports
- the parameter uuid, is created from the function_id, so in this way, makes the parameter_id tied to a function, and not necessarily an orphan parameter in isolation from the function. The parameter_id ties back to a specific function, even if the name and type are shared.

| field name     | type | description                                             |
| -------------- | ---- | ------------------------------------------------------- |
| `alias_map_id` | UUID | the UUID using `parameter_id` and `column_id` as inputs |
| `column_id`    | UUID | the ID from the `column_registry` table                 |
| `parameter_id` | UUID | the id from the `parameter` table                       |

## Validation Objects - python
- Python objects used to validate incoming data from a user, and cleanly push the create record entry into its respective table.

### SourceRegistryEntry
- tied to the `source_registry` table, and represents a python data object that validates any entries going into the `source_registry` table.
- will have all the fields that the `source_registry` table has, as it's the python validation reflection of the table.
- `SourceRegistryEntry` has a method that generates the `table_url` url, pointing to where the `source_registry` table is, and where the entry is going to get stored. Will update itself accordingly.
- Does not communicate with anything other than the cache table that gets created when the user is creating a new source, and maybe a config that has the database URL.
- `SourceRegistryEntry` also has a method that generates the `source_registry_id` UUID based on the `source_registry_id` logic in the `source_registry` table

### SourceRegistryUpdate
- Exactly the same as the `SourceRegistryEntry`, but all the fields are optional, so that when the user wants to make an update to a registry field, the update can be pushed without needing to validate everything over again, or the user doesn't need to refill everything, and just make changes to what they want to change
- Any request on updates, will go through this object instead of the `SourceRegistryEntry` objects.

### ColumnRegistryEntry
- tied to the `column_registry` table and has the same fields, since it's the Python validation layer of the table.
- Has a method that generates `column_id`, based on the logic outlined in the table. This gets uploaded into the table, and used as the table key.

### ColumnRegistryUpdate
- Exactly the same as the `ColumnRegistryEntry`, but all the fields are optional, so that when the user wants to make an update to a registry field, the update can be pushed without needing to validate everything over again, or the user doesn't need to refill everything, and just make changes to what they want to change
- Any request on updates, will go through this object instead of the `ColumnRegistryEntry` objects.

## Rejection Objects - python

### FailedRegistryEntry
- Used for entries that failed to be added to the table. This object is specific to registry tables (tables with `registry` in the name, that we have defined in this design doc). We don't have to specify that in the logic but something to keep in mind in order to keep responsibilities separate.
- Stores the table entry object (`SourceRegistryEntry`, `ColumnRegistryEntry`), which has the values of what the backend tried to add to the table. Having the object also tells the user which table the entry attempted to add to
- Stores the error message, and why the entry failed validation or failed to be added to the table

### FailedFunctionEntry
- Used for when a function entry gets rejected  for one reason or another. For example, if the return is missing, or if a parameter isn't typed.
- Stores everything pertaining to the Function and its break down
- Also stores the error message on why this was rejected and possible actions to help resolve the issue


## Function Objects - python
- for a lack of a better term, these are python objects that handle functions that the end user created and uploaded to the app, so that the python functions can be used in the pipeline to validate or transform the table and data



# Workflows and Features
So moving on from the data portion, this section will split the user experience and workflows by what the user experience should be and what the backend is focusing on accomplishing in that scenario in the backend. We'll start each subsection with a scenario of what I want the user experience to be, and what that may look like in the backend.

## Initializing a new source
This task is for when the user opens up the app, and wants to register a new report on the application. Once the user successfully registers a source, they can validate the data and use it for their final deliverables.

### User Perspective
1. Download the spreadsheet from their data source.
2. Upload the report to the app, via `create` -> `source` in the UI. The user can drag the file in, or use a file selector to multi-select as many files as they want
3. A confirmation window appears, asking whether to use the selection as a template, or to use it as a template, and attempt to ingest the data into the newly created table.
	1. if a user wants to add checks to the report before moving forward with ingestion, then they would select the template option
	2. if the user does not care to validate it when the data comes in, they go skip to ingest data after the creation
	3. The user will also get asked which of the columns is the primary key. The user will select a column from a list, and the selected column will be highlighted
	4. The user also gets asked how they want the ingestion process. Whether they want to `upsert` or `skip` on duplicate entries. More ingestion types will come in the future
4. After confirming their selection, the app loads. It's validating the data and the user sees the progress on the screen, which shows the number of spreadsheets they have selected, percentage complete, which files have completed and was successful, which files were not successful.
5. For the successful files, the user confirms, or edits, the data types to ensure they are correct. All subsequent uploads will use this data type as the source of truth. The user should be able to edit the data type at any time though via `edit` -> `source`
6. The source can now be used as a raw file to create deliverables and run checks on. The source is available to use and is selectable in different tabs, including the `Sources` and `Deliverable` tabs.
7. The users can then add the functions they created to the report by going to `Sources` -> `add validations and checks`, or the `Sources` page can have cards of registered `Sources`, where the user can hover over it and select edit or add checks.

### Backend Perspective
1. The backend reads the spreadsheets that the user has selected to upload. Reading the spreadsheet consists of the following:
	1. reads the filename and tries to create a regex `pattern`, which is a field in the `source_registry` table. This can be used in the future to identify new reports with the same convention being saved down
	2. The column names should be available when we read_csv or read_excel with duckdb, any columns that are not in the `column_registry` should be added to that table for future reference.
	3. should gather sample data from the uploaded report (should try to use duckdb native features) to try and infer the `column_type`. If we are not able to infer, either through an error or not enough data is available, then make it `var`. Alternatively, could try to search for the `column_name` in the `column_registry` table to try and infer the data type.
	4. We store what we have so far in a temporary cache, and request from the user the primary key column (3.3 from the previous User Perspective section)
		1. Since the user also updates the column data types as part of the request on the user side, any updates or changes the user makes there, should be reflected before the values get pulled into the python object.
		2. The user's final confirmation is the source of truth, and any changes the user makes, should also update the cache.
	5. The date modified can be gotten from `st_mtime` by using the file path  from the user report selection, or a faster way of getting this field.
	6. The backend should now have enough information, except for `table_url`, which we'll keep optional until the next step
2. The data from (1.1 - 1.6) is then pulled into a `SourceRegistryEntry` python object (dataclass or pydantic to ensure the information we received is validated) from the cache. Note that since column related data is not in the `source_registry` table, that information does not get pulled into the `SourceRegistryEntry` object.
	1. We validate each field to ensure the entry is correct, and will flow into the `source_registry` duckdb table smoothly.
	2. `SourceRegistryEntry` has a method that generates the `table_url` url, pointing to where the `source_registry` table is, and where the entry is going to get stored. Will update itself accordingly.
	3. `SourceRegistryEntry` also has a method that generates the `source_registry_id` UUID based on the `source_registry_id` logic in the `source_registry` table
3. Once all the fields are validated (if it fails, then we store the entire object in a `FailedRegistryEntry` stack object, which includes the error message, to give back to the UI as a failed upload), the backend writes an entry for the `source_registry` table.
		1. Once the entry is written, we should still have access to the database connection, the cache table, and have the `source_registry_id`. This is where we will fill out two more tables
			1. Using `ColumnRegistryEntry` to add an entry to the `column_registry` table and
			2. add an entry to the `source_column_map` relational table directly, without a python validation object inbetween since this is a relational table we are adding an entry to. We also won't add a python validation layer for subsequent data ingestions after this initial creation, since validations can be done through user functions.
4. The backend then, takes the column names from the read (should be in cache), as well as the user confirmed data types tied to the columns (should also be in the cache), and pulls it into the `ColumnRegistryEntry` object
	1. This object then validates each column data (each column is a separate column: column type instance), and either gets kicked into `FailedRegistryEntry` stack object if it errors, or an entry gets added to the `column_registry` table from the `ColumnRegistryEntry` object (pydantic or dataclass workflow)
5. The same steps as 3 and 4 are taken for the `source_column_map`, where the required information is available in the config or in the cache.
	1. Similar to 4, we add instance of the entry for each column, but what's different is that we do not add this, or put this through a python validation object.
	2. Add the `source_id` of the source that is currently being added, along with the `column_id` of the column being added to the registry table. The UUID can be created from a standalone UUID creation function here.
	3. This entry is then added to the table directly through a sql query, or duckdb query into the `source_column_map` table.
6. The app is now able to filter this table by the `report_id` to get the full list of the columns in the report. The columns then can be joined with the `column_registry` table to get more details and information on the columns.
	1. The user can make edits, and only the updated field will then funnel to the respective update python objects (`SourceRegistryUpdate`, `ColumnRegistryUpdate`, etc.). This python object, will then follow the same steps as the entry, but without needing to validate the fields that the user did not touch.
7. When the user actually wants to add records to the table, we can use a JIT approach, by creating a table based on `source_registry` and `column_registry` in order to write sql code that will create the correct table. Once this table is created, any file that the user uploads and ties to the the source, will get added directly via sql code.
	1. Sql code for these user generated files should be stored in a `sql_user_table` folder, and each user table should have it's own python module named after the table, with `sql` appended at the end (ie. `foo_source_sql.py`)

## Initializing and using a new user-created function

This is a multi-part task, but essentially, is where the end user will write functions in a python module (`.py`). The user can then upload their python modules (can have multiple `.py` files to upload, with multiple functions on each) to the application, so that their created functions can be used in the pipeline. This requires a lot of moving parts, and on the backend python side of the equation, is handled by objects in the `Function Objects - python` section of this doc.

Multiple tables also tie to these user generated functions.
- `function_registry`
- `parameter`
- `source_function_map`
- `alias_map`

### User Perspective - writing the function

1. The end user creates a `.py` text file, writing functions. We'll need a way to handle importing packages and dependencies that are in the module. User gets a warning if we can't handle or import a dependency.
	1. May require a toml file, or another way of handling dependencies that the user introduces, but the backend handles that and the user won't have to worry about it. Should be able to handle pandas by default.
2. The user then uploads the file in the application, likely under the `Function` tab or through `create` -> `function`
3. The `Function` tab also has all verified functions that the user previously uploaded. This allows for the user to have visibility on the functions that are available
4. The function needs to have:
	1. Typed parameters (ie. `Foo(raz: int, bar: str)`)
	2. Typed returns (ie. `Foo(raz: int, bar: str) -> str`)
	3. If any of the requirements above is not correct, then this should raise an error using the `FailedFunctionEntry` and returned to the app ui for the user to see.
	4. The process is repeated for each function in each module that the user uploaded to the app

### User Perspective - using the created function after upload
1. Once the function was validated, then they will be available for the user under the `Sources` -> `add function`, after selecting the report card they want to add the functions to. Alternatively, the user can go from `Functions` -> select the function -> `add to source`
2. From this new screen / page, the user can then, select the functions they would like to add to the report. The user sees the function's doc string, the parameter and types, the return type, and some form of a drag or checkbox, that will indicate that the user has selected that specific function.
3. After they have selected the function, the application will validate their choices (checking against the `alias map` to ensure that the columns on the report were properly mapped to the parameters), and if successful, the function will be tied to the report. On failures, the user will be notified why. For example, if the column and parameters were not mapped, there will be a message indicating that the function was not added because the parameter and columns were not mapped.

## Backend Perspective - function classification
1. Other function classifications are written under the `Function Objects - python` section of this design doc. This includes information on 
	1. `function_return_type`
		1. `scalar`
		2. `boolean`
		3. `matrix`
	2. `function_type`
		1. `transform`
		2. `validation`
	3. `function_signature`
		1. use to get the lowest "granularity" (most generic) from a function parameter signature. The numbered list below is in order from highest granularity to the lowest.
			1. `scalar`: a scalar here is any single value primitive parameter (`int`, `float`, `bool`) and `str` if the value is not tied to an `alias_map`.
			2. `column_backed`: a `str` typed parameter, where the `parameter` ties to a record in the `alias_map`. This allows the user to give column name as an input, and use the output.
			3. `pd.series`: has a `pandas series` as a parameter. The only thing that would be lower than this, that we will start with, is a `pd.DataFrame` parameter type
			4. `pd.dataframe`: has a `pandas dataframe` as a parameter.
		2. any granularity higher than a scalar, is `multi_select_eligible`. This means that we can have a stack of column arguments (`pd.Series`, `column_backed`, or `pd.DataFrame`) in the parameter, which can run in a loop until all arguments are used
			1. For example, if we define a function `def foo(bar: str)` where `bar` is a `column_backed` string. Let's say that the report we attach this to has 3 columns eligible as arguments for this parameter based on the `alias_map` (`raz`, `raz_bar`, `foo_bar`).
			2. We can essentially then, run this function 3 times on a single report, where we run the same function for each eligible argument, which results in 3 different results. These results get aggregated in the summary
			3. So we run the function 3 times for each eligible function when they were mapped together
